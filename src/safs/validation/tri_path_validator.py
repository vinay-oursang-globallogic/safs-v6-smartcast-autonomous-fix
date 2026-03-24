"""
SAFS v6.0 — Tri-Path Validator Orchestrator

Coordinates validation across three paths (α, β, γ) based on bug category.

Path Selection Rules:
- LOK I bugs: PATH α (QEMU) + PATH γ (On-Device recommended)
- HTML5 bugs: PATH β (Playwright) + PATH γ (On-Device recommended)
- CROSS_LAYER: All three paths (α + β + γ required)
- MEDIATEK: No validation (auto-escalate, no fix generated)

On-Device Required Categories:
- KEYDOWN_NOT_FIRED
- LOKI_APP_LAUNCH_FAILURE  
- WATCHFREE_DEEPLINK_LOSS (CROSS_LAYER)

Confidence Boost:
- On-device pass: +15-20% confidence
- Bug reproduction success: +10% confidence
"""

import asyncio
import logging
from typing import Dict, List, Optional

from ..log_analysis.models import (
    BugLayer,
    ErrorCategory,
    FixCandidate,
    JiraTicket,
    PipelineState,
)
from .models import (
    CandidateValidationResult,
    PathValidationResult,
    ValidationPath,
)
from .on_device_validator import OnDeviceValidator
from .playwright_validator import PlaywrightValidator
from .qemu_validator import QEMUValidator

logger = logging.getLogger(__name__)


class TriPathValidator:
    """
    Tri-path validation orchestrator.
    
    Routes validation to appropriate paths based on:
    - Bug layer (LOKI, HTML5, CROSS_LAYER)
    - Error category
    - TV availability
    
    Coordinates:
    - PATH α (QEMU): LOKi C++ unit tests + ASan/TSan
    - PATH β (Playwright): HTML5 app scenarios + companion mock
    - PATH γ (On-Device): Real TV validation via vizio-mcp
    """
    
    # Categories that REQUIRE on-device validation
    ON_DEVICE_REQUIRED = {
        ErrorCategory.KEYDOWN_NOT_FIRED,
        ErrorCategory.LOKI_APP_LAUNCH_FAILURE,
        ErrorCategory.WATCHFREE_DEEPLINK_LOSS,
    }
    
    def __init__(
        self,
        qemu_validator: Optional[QEMUValidator] = None,
        playwright_validator: Optional[PlaywrightValidator] = None,
        ondevice_validator: Optional[OnDeviceValidator] = None,
        tv_available: bool = True,
    ):
        """
        Initialize tri-path validator.
        
        Args:
            qemu_validator: QEMU validator instance
            playwright_validator: Playwright validator instance
            ondevice_validator: On-device validator instance
            tv_available: Whether dev TV is available on network
        """
        self.qemu_validator = qemu_validator or QEMUValidator()
        self.playwright_validator = playwright_validator or PlaywrightValidator()
        self.ondevice_validator = ondevice_validator or OnDeviceValidator()
        self.tv_available = tv_available
    
    async def validate_all(
        self,
        state: PipelineState,
        candidates: List[FixCandidate],
    ) -> List[CandidateValidationResult]:
        """
        Validate all fix candidates using appropriate paths.
        
        Args:
            state: Pipeline state with ticket, bug_layer, error_category
            candidates: Fix candidates to validate
            
        Returns:
            List of CandidateValidationResult (one per candidate)
        """
        logger.info(f"Validating {len(candidates)} candidates")
        
        # Guard: Check required stage outputs are present
        if not state.buglayer_result or not state.root_cause_result:
            logger.error(
                "Missing required stage outputs: "
                f"buglayer_result={state.buglayer_result is not None}, "
                f"root_cause_result={state.root_cause_result is not None}"
            )
            raise ValueError(
                "Validation requires completed BugLayerRouter (Stage 0) and "
                "RootCauseAnalysis (Stage 2) results"
            )
        
        logger.info(f"Bug layer: {state.buglayer_result.layer.value}")
        logger.info(f"Error category: {state.root_cause_result.error_category.value}")
        
        # Determine required validation paths
        required_paths = self._required_paths(
            state.buglayer_result.layer,
            state.root_cause_result.error_category,
        )
        
        logger.info(f"Required validation paths: {[p.value for p in required_paths]}")
        
        # Validate each candidate
        results = []
        for candidate in candidates:
            logger.info(f"\n{'='*60}")
            logger.info(f"Validating candidate: {candidate.fix_id}")
            logger.info(f"Strategy: {candidate.strategy.value}")
            logger.info(f"{'='*60}\n")
            
            result = await self.validate_candidate(
                candidate,
                state.ticket,
                state.buglayer_result.layer,
                state.root_cause_result.error_category,
                required_paths,
            )
            
            results.append(result)
        
        return results
    
    async def validate_candidate(
        self,
        candidate: FixCandidate,
        ticket: JiraTicket,
        bug_layer: BugLayer,
        error_category: ErrorCategory,
        required_paths: Optional[List[ValidationPath]] = None,
    ) -> CandidateValidationResult:
        """
        Validate single fix candidate.
        
        Args:
            candidate: Fix candidate
            ticket: Jira ticket
            bug_layer: Bug layer
            error_category: Error category
            required_paths: Override required paths (default: auto-determine)
            
        Returns:
            CandidateValidationResult with all path results
        """
        if required_paths is None:
            required_paths = self._required_paths(bug_layer, error_category)
        
        # Run validations in parallel (paths are independent)
        validation_tasks = {}
        
        if ValidationPath.ALPHA_QEMU in required_paths:
            validation_tasks["alpha"] = self.qemu_validator.validate(
                candidate, error_category
            )
        
        if ValidationPath.BETA_PLAYWRIGHT in required_paths:
            validation_tasks["beta"] = self.playwright_validator.validate(
                candidate,
                error_category,
                app_name=ticket.streaming_app if hasattr(ticket, "streaming_app") else None,
            )
        
        if ValidationPath.GAMMA_ONDEVICE in required_paths:
            validation_tasks["gamma"] = self.ondevice_validator.validate(
                candidate,
                ticket,
                bug_layer,
                error_category,
            )
        
        # Wait for all validations to complete
        path_results = await asyncio.gather(
            *validation_tasks.values(),
            return_exceptions=True,
        )
        
        # Map results to paths
        alpha_result = None
        beta_result = None
        gamma_result = None
        validation_exceptions = []
        
        for path_name, result in zip(validation_tasks.keys(), path_results):
            if isinstance(result, Exception):
                logger.error(f"PATH {path_name} validation failed: {result}")
                validation_exceptions.append(f"{path_name}: {str(result)}")
                continue
            
            if path_name == "alpha":
                alpha_result = result
            elif path_name == "beta":
                beta_result = result
            elif path_name == "gamma":
                gamma_result = result
        
        # Aggregate results
        # BUG FIX: If any required path threw an exception, validation fails
        # Previously: missing results defaulted to True, allowing false passes
        if validation_exceptions:
            all_passed = False
        else:
            all_passed = all([
                alpha_result.passed if alpha_result else True,
                beta_result.passed if beta_result else True,
                gamma_result.passed if gamma_result else True,
            ])
        
        # Compute validation score and confidence boost
        validation_score = self._compute_validation_score(
            alpha_result, beta_result, gamma_result, required_paths
        )
        
        confidence_boost = self._compute_confidence_boost(
            gamma_result, required_paths
        )
        
        # Total duration
        total_duration = sum([
            alpha_result.duration_seconds if alpha_result else 0,
            beta_result.duration_seconds if beta_result else 0,
            gamma_result.duration_seconds if gamma_result else 0,
        ])
        
        return CandidateValidationResult(
            candidate_id=candidate.fix_id,
            alpha_result=alpha_result,
            beta_result=beta_result,
            gamma_result=gamma_result,
            qemu_details=alpha_result.evidence.get("qemu_details") if alpha_result else None,
            playwright_details=beta_result.evidence.get("playwright_details") if beta_result else None,
            ondevice_details=gamma_result.evidence.get("ondevice_details") if gamma_result else None,
            overall_passed=all_passed,
            required_paths=required_paths,
            confidence_boost=confidence_boost,
            validation_score=validation_score,
            total_duration_seconds=total_duration,
        )
    
    def _required_paths(
        self,
        bug_layer: BugLayer,
        error_category: ErrorCategory,
    ) -> List[ValidationPath]:
        """
        Determine required validation paths for bug category.
        
        Args:
            bug_layer: Bug layer
            error_category: Error category
            
        Returns:
            List of required validation paths
        """
        paths = []
        
        # BUG FIX: MEDIATEK layer should not participate in validation
        # These are hardware/driver bugs that must be escalated to vendor
        if bug_layer == BugLayer.MEDIATEK:
            logger.info("MEDIATEK layer detected - no validation paths (auto-escalate)")
            return []  # Empty list = no validation, escalate to human
        
        # PATH α: QEMU for LOKi bugs
        if bug_layer in (BugLayer.LOKI, BugLayer.CROSS_LAYER):
            paths.append(ValidationPath.ALPHA_QEMU)
        
        # PATH β: Playwright for HTML5 bugs
        if bug_layer in (BugLayer.HTML5, BugLayer.CROSS_LAYER):
            paths.append(ValidationPath.BETA_PLAYWRIGHT)
        
        # PATH γ: On-Device
        # Required for specific categories or CROSS_LAYER
        if (error_category in self.ON_DEVICE_REQUIRED or 
            bug_layer == BugLayer.CROSS_LAYER):
            paths.append(ValidationPath.GAMMA_ONDEVICE)
        # Recommended for all others if TV available (but not MEDIATEK)
        elif self.tv_available and bug_layer != BugLayer.MEDIATEK:
            paths.append(ValidationPath.GAMMA_ONDEVICE)
        
        return paths
    
    def _compute_validation_score(
        self,
        alpha_result: Optional[PathValidationResult],
        beta_result: Optional[PathValidationResult],
        gamma_result: Optional[PathValidationResult],
        required_paths: List[ValidationPath],
    ) -> float:
        """
        Compute overall validation quality score (0.0-1.0).
        
        Weighting:
        - PATH α: 30%
        - PATH β: 30%
        - PATH γ: 40% (highest weight - ground truth)
        
        Args:
            alpha_result: QEMU result
            beta_result: Playwright result
            gamma_result: On-device result
            required_paths: Required paths
            
        Returns:
            Validation score (0.0-1.0)
        """
        score = 0.0
        total_weight = 0.0
        
        # PATH α weight
        if ValidationPath.ALPHA_QEMU in required_paths and alpha_result:
            weight = 0.30
            path_score = 1.0 if alpha_result.passed else 0.0
            
            # Partial credit for MTK_CURRENT only
            if alpha_result.evidence.get("qemu_details"):
                qemu_details = alpha_result.evidence["qemu_details"]
                if (qemu_details.get("mtk_current_passed") and 
                    not qemu_details.get("mtk_legacy_passed")):
                    path_score = 0.65  # 65% score (reflects -15% confidence penalty)
            
            score += weight * path_score
            total_weight += weight
        
        # PATH β weight
        if ValidationPath.BETA_PLAYWRIGHT in required_paths and beta_result:
            weight = 0.30
            path_score = 1.0 if beta_result.passed else 0.0
            score += weight * path_score
            total_weight += weight
        
        # PATH γ weight (highest)
        if ValidationPath.GAMMA_ONDEVICE in required_paths and gamma_result:
            weight = 0.40
            path_score = 1.0 if gamma_result.passed else 0.0
            
            # Bonus for successful bug reproduction
            if gamma_result.evidence.get("ondevice_details"):
                ondevice_details = gamma_result.evidence["ondevice_details"]
                if ondevice_details.get("reproduction_successful"):
                    path_score = min(1.0, path_score + 0.10)  # +10% for repro
            
            score += weight * path_score
            total_weight += weight
        
        # Normalize score
        if total_weight > 0:
            return score / total_weight
        else:
            return 0.0
    
    def _compute_confidence_boost(
        self,
        gamma_result: Optional[PathValidationResult],
        required_paths: List[ValidationPath],
    ) -> float:
        """
        Compute confidence boost from validation results.
        
        On-device validation pass: +0.15 to +0.20
        Bug reproduction success: +0.10
        
        Args:
            gamma_result: On-device result
            required_paths: Required paths
            
        Returns:
            Confidence boost (0.0-0.30)
        """
        boost = 0.0
        
        # On-device validation pass boost
        if ValidationPath.GAMMA_ONDEVICE in required_paths and gamma_result:
            if gamma_result.passed:
                boost += 0.15  # Base boost for on-device pass
                
                # Extra boost for bug reproduction success
                if gamma_result.evidence.get("ondevice_details"):
                    ondevice_details = gamma_result.evidence["ondevice_details"]
                    if ondevice_details.get("reproduction_successful"):
                        boost += 0.10  # +10% for successful repro
        
        return min(boost, 0.30)  # Cap at +30%
