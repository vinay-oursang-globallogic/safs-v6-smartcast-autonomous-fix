"""
SAFS v6.0 — Confidence Ensemble (Stage 7.5)

4-signal weighted ensemble that combines multiple confidence indicators
to produce a calibrated confidence score and routing decision.

Extended from jira_auto_fixer/learning_system.py which provides:
- SQLite-backed A/B testing of models and prompts
- Success rate tracking per bug type
- Cost and duration tracking

v6.0 additions:
- On-device validation pass adds +15-20% confidence boost
- Bug reproduction success adds +10% confidence boost
- Platt scaling from production outcomes (monthly recalibration)
- 4-signal weighted ensemble for robust confidence estimation

Master Prompt Reference: Section 3.11 - Stage 7.5: Confidence Ensemble
Architecture Review Reference: Improvement 1 - On-Device Validation Path
"""

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from ..log_analysis.models import (
    BugLayer,
    ConfidenceRouting,
    ErrorCategory,
    FixCandidate,
)
from ..validation.models import CandidateValidationResult

logger = logging.getLogger(__name__)


# ============================================================================
# MODELS
# ============================================================================


class ConfidenceSignals(BaseModel):
    """
    Input signals for confidence ensemble.
    
    Four primary signals:
    1. LLM self-assessment (from fix generation)
    2. Retrieval similarity (best Qdrant match)
    3. Validation score (tri-path validation results)
    4. Historical success rate (from learning system)
    
    Bonus signals:
    - Reproduction bonus: bug was reproduced pre-fix
    - On-device passed: PATH γ validation passed
    """
    llm_confidence: float = Field(
        ..., ge=0.0, le=1.0,
        description="LLM self-assessment from fix generation"
    )
    retrieval_similarity: float = Field(
        0.0, ge=0.0, le=1.0,
        description="Best Qdrant historical fix match similarity"
    )
    validation_score: float = Field(
        0.0, ge=0.0, le=1.0,
        description="Validation quality score (0.0 if not run)"
    )
    historical_success_rate: float = Field(
        0.5, ge=0.0, le=1.0,
        description="Success rate for this error category (default 0.5)"
    )
    reproduction_bonus: float = Field(
        0.0, ge=0.0, le=0.15,
        description="Bonus if bug was reproduced (0.10 typical)"
    )
    on_device_passed: bool = Field(
        False,
        description="Whether on-device validation (PATH γ) passed"
    )
    on_device_boost: float = Field(
        0.0, ge=0.0, le=0.20,
        description="Confidence boost from on-device validation (0.15 typical)"
    )
    
    # Metadata for analysis
    bug_layer: Optional[BugLayer] = Field(None, description="Bug layer")
    error_category: Optional[ErrorCategory] = Field(None, description="Error category")


class ConfidenceResult(BaseModel):
    """
    Output from confidence ensemble.
    
    Contains calibrated confidence score, routing decision, and
    detailed signal breakdown for debugging.
    """
    raw_score: float = Field(
        ..., ge=0.0, le=1.5,
        description="Raw weighted ensemble score before calibration"
    )
    calibrated_score: float = Field(
        ..., ge=0.0, le=1.0,
        description="Platt-scaled calibrated confidence score"
    )
    routing: ConfidenceRouting = Field(
        ...,
        description="Routing decision based on calibrated score"
    )
    signals: ConfidenceSignals = Field(
        ...,
        description="Input signals used for ensemble"
    )
    signal_weights: Dict[str, float] = Field(
        default_factory=dict,
        description="Weights applied to each signal"
    )
    computed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp of confidence computation"
    )


# ============================================================================
# CONFIDENCE ENSEMBLE
# ============================================================================


class ConfidenceEnsemble:
    """
    4-signal weighted ensemble with Platt scaling calibration.
    
    Signal Weights (tuned from v5.1.1 production data):
    - llm_confidence: 0.20 (LLM self-assessment)
    - retrieval_similarity: 0.20 (Best Qdrant historical fix match)
    - validation_score: 0.35 (Validation results - highest weight)
    - historical_success_rate: 0.15 (From learning_system.py data)
    - reproduction_bonus: 0.10 (Bug was reproduced pre-fix)
    
    Bonuses:
    - On-device validation pass: +0.15 to +0.20
    - Bug reproduction success: +0.10
    - Total max boost: +0.30
    
    Routing Thresholds (calibrated):
    - >= 0.85: AUTO_PR (auto-merge if tests pass)
    - >= 0.65: PR_WITH_REVIEW_REQUIRED (requires human review)
    - >= 0.45: ANALYSIS_ONLY (post analysis, no PR)
    - < 0.45: ESCALATE_HUMAN (insufficient data, escalate)
    """
    
    # Signal weights (sum to 1.0)
    WEIGHTS = {
        "llm_confidence": 0.20,
        "retrieval_similarity": 0.20,
        "validation_score": 0.35,  # Highest weight - validation is ground truth
        "historical_success_rate": 0.15,
        "reproduction_bonus": 0.10,
    }
    
    # Routing thresholds (calibrated from production data)
    ROUTING_THRESHOLDS = {
        ConfidenceRouting.AUTO_PR: 0.85,
        ConfidenceRouting.PR_WITH_REVIEW: 0.65,
        ConfidenceRouting.ANALYSIS_ONLY: 0.45,
        # Below 0.45 = ESCALATE_HUMAN
    }
    
    # Platt scaling parameters (will be updated from production feedback)
    # Format: platt_a * raw_score + platt_b
    # These are initialized to identity (no scaling), will be trained
    PLATT_A = 1.0
    PLATT_B = 0.0
    
    def __init__(
        self,
        db_path: Optional[Path] = None,
        enable_learning: bool = True,
    ):
        """
        Initialize confidence ensemble.
        
        Args:
            db_path: Path to SQLite database for learning system
                     (default: ~/.safs/learning.db)
            enable_learning: Whether to track outcomes for learning
        """
        self.db_path = db_path or Path.home() / ".safs" / "learning.db"
        self.enable_learning = enable_learning
        
        if enable_learning:
            self._init_db()
        
        logger.info(
            f"ConfidenceEnsemble initialized "
            f"(learning={'enabled' if enable_learning else 'disabled'})"
        )
    
    def _init_db(self) -> None:
        """Initialize SQLite database for learning system."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()
        
        # Table: confidence_history
        # Tracks all confidence computations and their outcomes
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS confidence_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_key TEXT NOT NULL,
                candidate_id TEXT NOT NULL,
                bug_layer TEXT,
                error_category TEXT,
                llm_confidence REAL,
                retrieval_similarity REAL,
                validation_score REAL,
                historical_success_rate REAL,
                reproduction_bonus REAL,
                on_device_passed INTEGER,
                on_device_boost REAL,
                raw_score REAL,
                calibrated_score REAL,
                routing TEXT,
                outcome TEXT,  -- SUCCESS, REVERTED, MERGED, REJECTED
                pr_url TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                outcome_at TIMESTAMP
            )
        """)
        
        # Table: category_success_rates
        # Aggregated success rates per error category
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS category_success_rates (
                error_category TEXT PRIMARY KEY,
                total_attempts INTEGER DEFAULT 0,
                successful_fixes INTEGER DEFAULT 0,
                success_rate REAL DEFAULT 0.5,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Table: platt_calibration
        # Stores Platt scaling parameters (updated monthly)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS platt_calibration (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platt_a REAL NOT NULL,
                platt_b REAL NOT NULL,
                calibrated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                sample_count INTEGER,
                notes TEXT
            )
        """)
        
        conn.commit()
        conn.close()
        
        logger.info(f"Learning database initialized at {self.db_path}")
    
    def compute(
        self,
        signals: ConfidenceSignals,
    ) -> ConfidenceResult:
        """
        Compute calibrated confidence score and routing decision.
        
        Args:
            signals: Input confidence signals
            
        Returns:
            ConfidenceResult with calibrated score and routing
        """
        # Step 1: Compute raw weighted ensemble score
        raw_score = 0.0
        
        for signal_name, weight in self.WEIGHTS.items():
            signal_value = getattr(signals, signal_name, 0.0)
            raw_score += weight * signal_value
        
        # Step 2: Add on-device validation bonus
        if signals.on_device_passed and signals.on_device_boost > 0:
            raw_score = min(1.5, raw_score + signals.on_device_boost)
            logger.info(
                f"On-device validation passed: +{signals.on_device_boost:.2f} boost "
                f"(raw_score now {raw_score:.3f})"
            )
        
        # Step 3: Apply Platt scaling for calibration
        calibrated = self._platt_scale(raw_score)
        
        # Step 4: Determine routing decision
        routing = self._route(calibrated)
        
        logger.info(
            f"Confidence computed: raw={raw_score:.3f}, "
            f"calibrated={calibrated:.3f}, routing={routing.value}"
        )
        
        return ConfidenceResult(
            raw_score=raw_score,
            calibrated_score=calibrated,
            routing=routing,
            signals=signals,
            signal_weights=self.WEIGHTS,
        )
    
    def _platt_scale(self, raw_score: float) -> float:
        """
        Apply Platt scaling for probability calibration.
        
        Platt scaling: P = 1 / (1 + exp(-(A * raw_score + B)))
        
        For initial deployment, A=1.0, B=0.0 (identity function).
        After collecting production outcomes, we can train A and B
        to minimize log loss.
        
        Args:
            raw_score: Raw ensemble score (0.0-1.5 range)
            
        Returns:
            Calibrated probability (0.0-1.0 range)
        """
        import math
        
        # Linear transformation
        z = self.PLATT_A * raw_score + self.PLATT_B
        
        # Sigmoid to map to [0, 1]
        try:
            calibrated = 1.0 / (1.0 + math.exp(-z))
        except OverflowError:
            # Handle extreme values
            calibrated = 1.0 if z > 0 else 0.0
        
        # Clamp to valid range
        return max(0.0, min(1.0, calibrated))
    
    def _route(self, confidence: float) -> ConfidenceRouting:
        """
        Determine routing decision based on calibrated confidence.
        
        Thresholds:
        - >= 0.85: AUTO_PR (auto-merge if tests pass)
        - >= 0.65: PR_WITH_REVIEW (requires human review)
        - >= 0.45: ANALYSIS_ONLY (post analysis, no PR)
        - < 0.45: ESCALATE_HUMAN (insufficient data, escalate)
        
        Args:
            confidence: Calibrated confidence score (0.0-1.0)
            
        Returns:
            ConfidenceRouting decision
        """
        if confidence >= self.ROUTING_THRESHOLDS[ConfidenceRouting.AUTO_PR]:
            return ConfidenceRouting.AUTO_PR
        elif confidence >= self.ROUTING_THRESHOLDS[ConfidenceRouting.PR_WITH_REVIEW]:
            return ConfidenceRouting.PR_WITH_REVIEW
        elif confidence >= self.ROUTING_THRESHOLDS[ConfidenceRouting.ANALYSIS_ONLY]:
            return ConfidenceRouting.ANALYSIS_ONLY
        else:
            return ConfidenceRouting.ESCALATE_HUMAN
    
    def get_historical_success_rate(
        self,
        error_category: ErrorCategory,
    ) -> float:
        """
        Get historical success rate for an error category.
        
        Args:
            error_category: Error category
            
        Returns:
            Success rate (0.0-1.0), defaults to 0.5 if no history
        """
        if not self.enable_learning:
            return 0.5
        
        try:
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT success_rate
                FROM category_success_rates
                WHERE error_category = ?
            """, (error_category.value,))
            
            row = cursor.fetchone()
            conn.close()
            
            if row:
                return row[0]
            else:
                return 0.5  # Default for new categories
                
        except Exception as e:
            logger.warning(f"Failed to fetch historical success rate: {e}")
            return 0.5
    
    def record_outcome(
        self,
        ticket_key: str,
        candidate_id: str,
        result: ConfidenceResult,
        outcome: str,  # SUCCESS, REVERTED, MERGED, REJECTED
        pr_url: Optional[str] = None,
    ) -> None:
        """
        Record confidence computation outcome for learning.
        
        Args:
            ticket_key: Jira ticket key
            candidate_id: Fix candidate ID
            result: ConfidenceResult from compute()
            outcome: Outcome status
            pr_url: PR URL if created
        """
        if not self.enable_learning:
            return
        
        try:
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO confidence_history (
                    ticket_key, candidate_id, bug_layer, error_category,
                    llm_confidence, retrieval_similarity, validation_score,
                    historical_success_rate, reproduction_bonus,
                    on_device_passed, on_device_boost,
                    raw_score, calibrated_score, routing,
                    outcome, pr_url, outcome_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                ticket_key,
                candidate_id,
                result.signals.bug_layer.value if result.signals.bug_layer else None,
                result.signals.error_category.value if result.signals.error_category else None,
                result.signals.llm_confidence,
                result.signals.retrieval_similarity,
                result.signals.validation_score,
                result.signals.historical_success_rate,
                result.signals.reproduction_bonus,
                1 if result.signals.on_device_passed else 0,
                result.signals.on_device_boost,
                result.raw_score,
                result.calibrated_score,
                result.routing.value,
                outcome,
                pr_url,
                datetime.now(timezone.utc).isoformat(),
            ))
            
            # Update category success rates if outcome is final
            if outcome in ("SUCCESS", "MERGED"):
                self._update_category_success_rate(
                    cursor,
                    result.signals.error_category,
                    success=True,
                )
            elif outcome in ("REVERTED", "REJECTED"):
                self._update_category_success_rate(
                    cursor,
                    result.signals.error_category,
                    success=False,
                )
            
            conn.commit()
            conn.close()
            
            logger.info(
                f"Recorded outcome for {ticket_key}/{candidate_id}: {outcome}"
            )
            
        except Exception as e:
            logger.error(f"Failed to record outcome: {e}")
    
    def _update_category_success_rate(
        self,
        cursor: sqlite3.Cursor,
        error_category: Optional[ErrorCategory],
        success: bool,
    ) -> None:
        """Update success rate for error category."""
        if not error_category:
            return
        
        category_str = error_category.value
        
        # Insert or update
        cursor.execute("""
            INSERT INTO category_success_rates (
                error_category, total_attempts, successful_fixes, success_rate
            ) VALUES (?, 1, ?, ?)
            ON CONFLICT(error_category) DO UPDATE SET
                total_attempts = total_attempts + 1,
                successful_fixes = successful_fixes + ?,
                success_rate = CAST(successful_fixes + ? AS REAL) / (total_attempts + 1),
                last_updated = CURRENT_TIMESTAMP
        """, (
            category_str,
            1 if success else 0,
            1 if success else 0,
            1 if success else 0,
            1 if success else 0,
        ))
    
    def update_platt_parameters(
        self,
        platt_a: float,
        platt_b: float,
        sample_count: int,
        notes: str = "",
    ) -> None:
        """
        Update Platt scaling parameters from offline calibration.
        
        Args:
            platt_a: New A parameter
            platt_b: New B parameter
            sample_count: Number of samples used for calibration
            notes: Optional notes about calibration
        """
        self.PLATT_A = platt_a
        self.PLATT_B = platt_b
        
        if self.enable_learning:
            try:
                conn = sqlite3.connect(str(self.db_path))
                cursor = conn.cursor()
                
                cursor.execute("""
                    INSERT INTO platt_calibration (
                        platt_a, platt_b, sample_count, notes
                    ) VALUES (?, ?, ?, ?)
                """, (platt_a, platt_b, sample_count, notes))
                
                conn.commit()
                conn.close()
                
                logger.info(
                    f"Updated Platt parameters: A={platt_a:.4f}, B={platt_b:.4f} "
                    f"(samples={sample_count})"
                )
                
            except Exception as e:
                logger.error(f"Failed to store Platt parameters: {e}")


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def build_confidence_signals(
    candidate: FixCandidate,
    validation_result: Optional[CandidateValidationResult],
    historical_success_rate: float,
    retrieval_similarity: float = 0.0,
    reproduction_successful: bool = False,
    bug_layer: Optional[BugLayer] = None,
    error_category: Optional[ErrorCategory] = None,
) -> ConfidenceSignals:
    """
    Build ConfidenceSignals from pipeline state and validation results.
    
    This is a helper function to assemble all signals from various
    pipeline stages into a single ConfidenceSignals object.
    
    Args:
        candidate: Fix candidate with LLM confidence
        validation_result: Validation results (if validation was run)
        historical_success_rate: Success rate from learning system
        retrieval_similarity: Best Qdrant match similarity
        reproduction_successful: Whether bug was reproduced
        bug_layer: Bug layer
        error_category: Error category
        
    Returns:
        ConfidenceSignals ready for ensemble computation
    """
    # Extract validation signals
    validation_score = 0.0
    on_device_passed = False
    on_device_boost = 0.0
    
    if validation_result:
        validation_score = validation_result.validation_score
        on_device_boost = validation_result.confidence_boost
        
        # Check if on-device validation passed
        if validation_result.gamma_result:
            on_device_passed = validation_result.gamma_result.passed
    
    # Reproduction bonus
    reproduction_bonus = 0.10 if reproduction_successful else 0.0
    
    return ConfidenceSignals(
        llm_confidence=candidate.confidence,
        retrieval_similarity=retrieval_similarity,
        validation_score=validation_score,
        historical_success_rate=historical_success_rate,
        reproduction_bonus=reproduction_bonus,
        on_device_passed=on_device_passed,
        on_device_boost=on_device_boost,
        bug_layer=bug_layer,
        error_category=error_category,
    )
