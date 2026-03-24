"""
Telemetry Module Models

Data models for async post-PR monitoring and proactive detection.

Models:
- MergedPR: Metadata about merged pull requests
- TelemetryMetric: Production telemetry data point
- RegressionAlert: Regression detection result
- ProactiveTicket: Auto-generated ticket from proactive monitoring
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field

from safs.log_analysis.models import BugLayer


class MistakeSeverity(str, Enum):
    """Severity of fix mistakes/regressions."""
    PRODUCTION_REGRESSION = "production_regression"
    DEVELOPER_CORRECTION = "developer_correction"
    TEST_FAILURE = "test_failure"
    VALIDATION_OVERRIDE = "validation_override"


class MergedPR(BaseModel):
    """Metadata about a merged pull request for regression monitoring."""
    
    pr_url: str
    pr_number: int
    ticket_id: str
    merged_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    # Bug context
    bug_layer: Optional[BugLayer] = None
    error_category: str
    app: Optional[str] = None  # e.g., "netflix", "hulu"
    chipset: Optional[str] = None  # e.g., "mt5670", "mt5882"
    
    # Fix metadata
    strategy: str  # SURGICAL, DEFENSIVE, REFACTORED
    confidence: float
    
    # Repository info
    repo: str
    branch: str
    commit_sha: Optional[str] = None


class TelemetryMetric(BaseModel):
    """Production telemetry metric."""
    
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    # Dimensions
    dimension: str  # "app", "layer", "chipset", "error_category"
    value: str  # e.g., "netflix", "loki", "mt5670"
    
    # Metrics
    error_rate: float  # errors per hour
    affected_users: int
    error_count: int
    total_events: int
    
    # Context
    firmware_versions: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RegressionAlert(BaseModel):
    """Alert for detected production regression."""
    
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    # Source PR
    pr_url: str
    ticket_id: str
    merged_at: datetime
    
    # Regression details
    error_category: str
    baseline_rate: float
    current_rate: float
    spike_factor: float  # current / baseline
    
    # Context
    affected_users: int
    dimension: str
    value: str
    
    # Actions taken
    jira_comment_added: bool = False
    revert_recommended: bool = False
    correction_saved: bool = False


class ProactiveTicket(BaseModel):
    """Auto-generated ticket from proactive telemetry monitoring."""
    
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    # Detection details
    dimension: str
    value: str
    baseline_rate: float
    current_rate: float
    spike_factor: float
    
    # Impact
    affected_users: int
    error_count: int
    duration_minutes: int
    
    # Ticket info
    jira_ticket_key: Optional[str] = None
    title: str
    description: str
    priority: str = "high"  # high, medium, low
    
    # Context
    firmware_versions: List[str] = Field(default_factory=list)
    related_errors: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class FixCorrection(BaseModel):
    """Record of a fix that needed correction."""
    
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    # Original fix
    original_ticket: str
    original_pr_url: Optional[str] = None
    
    # Correction details
    severity: MistakeSeverity
    correction_description: str
    
    # Regression data (if applicable)
    spike_factor: Optional[float] = None
    baseline_rate: Optional[float] = None
    current_rate: Optional[float] = None
    
    # Correction action
    corrected_by: Optional[str] = None  # developer who corrected
    correction_ticket: Optional[str] = None
    reverted: bool = False
    
    # Learning metadata
    error_category: str
    bug_layer: Optional[BugLayer] = None
    embedding: Optional[List[float]] = None  # For Qdrant storage
