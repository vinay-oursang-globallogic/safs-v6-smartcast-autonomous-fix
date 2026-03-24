"""
SAFS v6.0 — Qdrant Collection Models

Pydantic models for Qdrant institutional memory collections.
Two collections:
- historical_fixes: Past successful fixes with diffs
- fix_corrections: Past mistakes, developer corrections, regressions
"""

from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class FixRecord(BaseModel):
    """
    Record stored in historical_fixes collection.
    Represents a successful fix that was merged and validated.
    """
    # Identifiers
    fix_id: str = Field(..., description="Unique fix identifier (UUID)")
    jira_ticket: str = Field(..., description="Original Jira ticket key")
    pr_url: str = Field(..., description="GitHub/GitLab PR URL")
    commit_sha: str = Field(..., description="Git commit SHA")
    
    # Classification
    bug_layer: str = Field(..., description="BugLayer (LOKI, HTML5, MEDIATEK, CROSS_LAYER)")
    error_category: str = Field(..., description="One of 27 ErrorCategory values")
    
    # Context
    description: str = Field(..., description="Bug description for vector embedding")
    root_cause: str = Field(..., description="Root cause analysis summary")
    fix_strategy: str = Field(..., description="Fix strategy applied")
    
    # Code changes
    files_changed: List[str] = Field(default_factory=list, description="List of file paths")
    diff: str = Field(..., description="Git diff of the fix")
    lines_added: int = Field(default=0, description="Total lines added")
    lines_removed: int = Field(default=0, description="Total lines removed")
    
    # Metadata
    created_at: str = Field(..., description="Fix creation timestamp (ISO8601)")
    validated_at: Optional[str] = Field(None, description="Validation completion timestamp")
    validation_success: bool = Field(default=False, description="Whether validation passed")
    
    # Metrics
    confidence_score: float = Field(default=0.0, description="Original confidence score")
    validation_method: str = Field(default="QEMU", description="Validation path (QEMU, PLAYWRIGHT, ON_DEVICE)")
    regression_detected: bool = Field(default=False, description="Whether regression was later detected")
    
    # Tags
    tags: List[str] = Field(default_factory=list, description="Searchable tags")
    related_tickets: List[str] = Field(default_factory=list, description="Related Jira tickets")


class CorrectionRecord(BaseModel):
    """
    Record stored in fix_corrections collection.
    Represents a mistake, regression, or developer correction.
    """
    # Identifiers
    correction_id: str = Field(..., description="Unique correction identifier (UUID)")
    original_fix_id: Optional[str] = Field(None, description="Reference to original FixRecord (if applicable)")
    jira_ticket: str = Field(..., description="Jira ticket for the correction")
    
    # Classification
    error_category: str = Field(..., description="One of 27 ErrorCategory values")
    mistake_type: str = Field(..., description="REGRESSION, INCOMPLETE_FIX, LOGIC_ERROR, etc.")
    
    # Context
    description: str = Field(..., description="Description of the mistake for vector embedding")
    what_went_wrong: str = Field(..., description="Detailed explanation of the mistake")
    correct_approach: str = Field(..., description="What should have been done")
    
    # Code
    incorrect_code: Optional[str] = Field(None, description="The incorrect code snippet")
    correct_code: Optional[str] = Field(None, description="The correct code snippet")
    
    # Metadata
    created_at: str = Field(..., description="Correction creation timestamp (ISO8601)")
    detected_by: str = Field(default="DEVELOPER", description="How detected (DEVELOPER, TELEMETRY, REGRESSION_TEST)")
    severity: str = Field(default="MEDIUM", description="Mistake severity (LOW, MEDIUM, HIGH, CRITICAL)")
    
    # Learning
    lesson_learned: str = Field(default="", description="Key lesson from this mistake")
    prevention_checklist: List[str] = Field(default_factory=list, description="Checklist to prevent recurrence")
    
    # Metrics
    time_to_detect_hours: Optional[float] = Field(None, description="Hours between fix and detection")
    impacted_tickets: List[str] = Field(default_factory=list, description="Tickets affected by this mistake")


class SearchQuery(BaseModel):
    """
    Query for searching Qdrant collections.
    """
    text: str = Field(..., description="Query text for semantic search")
    bug_layer: Optional[str] = Field(None, description="Filter by BugLayer")
    error_category: Optional[str] = Field(None, description="Filter by ErrorCategory")
    top_k: int = Field(default=5, ge=1, le=20, description="Number of results to return")
    
    # Advanced filters
    min_confidence: Optional[float] = Field(None, ge=0.0, le=1.0, description="Minimum confidence score")
    validation_method: Optional[str] = Field(None, description="Filter by validation method")
    exclude_regressions: bool = Field(default=True, description="Exclude fixes with detected regressions")
    max_age_days: Optional[int] = Field(None, ge=0, description="Maximum age in days")


class SearchResult(BaseModel):
    """
    Single search result from Qdrant.
    """
    # Scoring
    score: float = Field(..., description="Combined RRF score")
    sparse_score: Optional[float] = Field(None, description="BM25 sparse score")
    dense_score: Optional[float] = Field(None, description="voyage-code-3 dense score")
    temporal_score: Optional[float] = Field(None, description="Score after temporal decay")
    
    # Metadata
    age_days: Optional[int] = Field(None, description="Age in days")
    decay_weight: Optional[float] = Field(None, description="Temporal decay multiplier")
    
    # Record (FixRecord or CorrectionRecord payload)
    record: Dict = Field(..., description="Qdrant point payload")


class RRFFusionConfig(BaseModel):
    """
    Configuration for RRF (Reciprocal Rank Fusion) algorithm.
    """
    k: int = Field(default=60, description="RRF constant (controls score compression)")
    sparse_weight: float = Field(default=0.5, ge=0.0, le=1.0, description="Weight for BM25 sparse scores")
    dense_weight: float = Field(default=0.5, ge=0.0, le=1.0, description="Weight for dense scores")
    prefetch_limit_multiplier: int = Field(default=3, ge=1, description="Fetch N×top_k for each index")


class TemporalDecayConfig(BaseModel):
    """
    Configuration for temporal decay re-ranking.
    """
    # Category-specific decay half-lives (days)
    halflife_days: Dict[str, int] = Field(
        default={
            "COMPANION_LIB_TIMING": 90,
            "EME_DRM_FAILURE": 120,
            "SHAKA_ERROR_3016": 180,
            "NETFLIX_MSL_TIMEOUT": 365,
            "LOKI_SEGFAULT_NULL_DEREF": 730,
            "LOKI_RACE_CONDITION": 730,
            "LOKI_MEMORY_CORRUPTION": 730,
            "DEFAULT": 365,
        },
        description="Half-life in days by error category"
    )
    
    def get_halflife(self, error_category: str) -> int:
        """Get half-life for a specific error category."""
        return self.halflife_days.get(error_category, self.halflife_days["DEFAULT"])
