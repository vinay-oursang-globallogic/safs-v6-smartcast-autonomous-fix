"""
Reproduction Data Models
========================

Pydantic models for Stage 5.5: Bug Reproduction.

These models support on-device validation via vizio-mcp MCP servers.
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any, Literal
from enum import Enum


class CompanionLibInfo(BaseModel):
    """
    Live Companion Library information resolved from dev TV system registry.
    
    Replaces static CompanionLibVersionMatrix with dynamic runtime resolution.
    Queried via vizio-ssh MCP server from paths:
    - /app/loki/version (LOKi version)
    - /os/version/firmware (Firmware version)
    - /hw/chipset/model (Chipset model)
    - /app/loki/config/companion-server-enabled (Companion enabled flag)
    - /app/cobalt/version (Chromium/Cobalt version)
    """
    
    loki_version: str = Field(..., description="LOKi version (e.g., '3.2.1')")
    firmware_version: str = Field(..., description="Firmware version (e.g., '5.2.1')")
    chipset: str = Field(..., description="Chipset model (e.g., 'MT5882')")
    companion_enabled: bool = Field(..., description="Companion server enabled")
    chromium_version: Optional[str] = Field(None, description="Chromium/Cobalt version")
    companion_api_version: str = Field(
        ..., description="Derived companion API version (e.g., 'v3.2')"
    )
    

class ReproductionStrategy(str, Enum):
    """
    Bug reproduction strategy.
    
    - DETERMINISTIC: Follow explicit reproduction steps from Jira ticket
    - EXPLORATORY: Launch affected app and wait for error to manifest
    - SKIP: No reproduction attempted (TV unavailable or firmware mismatch)
    """
    
    DETERMINISTIC = "deterministic"
    EXPLORATORY = "exploratory"
    SKIP = "skip"


class ReproductionStatus(str, Enum):
    """
    Result status of bug reproduction attempt.
    
    - REPRODUCED: Bug error manifested in logs/UI
    - NOT_REPRODUCED: Bug did not manifest after reproduction steps
    - SKIP: Reproduction not attempted (incompatible firmware, no TV, etc.)
    """
    
    REPRODUCED = "REPRODUCED"
    NOT_REPRODUCED = "NOT_REPRODUCED"
    SKIP = "SKIP"


class ReproductionEvidence(BaseModel):
    """
    Evidence captured during bug reproduction attempt.
    
    Used for before/after comparison during validation and for
    enriching the Fix Generator context.
    """
    
    logs: str = Field(default="", description="Captured log output (last 5 min)")
    screenshot: Optional[str] = Field(None, description="Screenshot filename or base64")
    scene_graph: Optional[Dict[str, Any]] = Field(
        None, description="LOKi scene graph JSON"
    )
    error_count: int = Field(default=0, description="Number of target errors found")
    matched_patterns: List[str] = Field(
        default_factory=list, description="Error patterns that matched"
    )
    

class BaselineMetrics(BaseModel):
    """
    Baseline system metrics captured during bug reproduction.
    
    Used for before/after comparison to detect regressions or improvements.
    """
    
    loki_memory_mb: Optional[float] = Field(None, description="LOKi process memory (MB)")
    chromium_memory_mb: Optional[float] = Field(
        None, description="Chromium process memory (MB)"
    )
    cpu_percent: Optional[float] = Field(None, description="System CPU utilization %")
    error_rate_per_min: float = Field(default=0.0, description="Errors per minute")
    crash_count: int = Field(default=0, description="Process crash count")


class ReproStep(BaseModel):
    """
    Single reproduction step parsed from Jira ticket.
    
    Examples:
    - {"action": "launch_app", "params": {"app_name": "Netflix"}}
    - {"action": "send_key", "params": {"key": "Down"}}
    - {"action": "wait", "params": {"seconds": 5}}
    """
    
    action: str = Field(..., description="Action type (launch_app, send_key, wait)")
    params: Dict[str, Any] = Field(
        default_factory=dict, description="Action parameters"
    )
    description: Optional[str] = Field(None, description="Human-readable description")


# Re-export for backward compatibility (if ReproResult used elsewhere)
class ReproResultV2(BaseModel):
    """
    Stage 5.5: Bug Reproduction Result (v6.0 format).
    
    This is the NEW format matching the master prompt specification.
    The old ReproResult in log_analysis/models.py is deprecated.
    """
    
    status: ReproductionStatus = Field(
        ..., description="Reproduction status (REPRODUCED/NOT_REPRODUCED/SKIP)"
    )
    strategy: ReproductionStrategy = Field(
        ..., description="Strategy used for reproduction"
    )
    reason: Optional[str] = Field(
        None, description="Reason for SKIP status (e.g., 'Firmware mismatch')"
    )
    
    # Evidence
    evidence: ReproductionEvidence = Field(
        default_factory=ReproductionEvidence,
        description="Captured evidence (logs, screenshot, scene graph)"
    )
    
    # System info
    companion_info: Optional[CompanionLibInfo] = Field(
        None, description="Live companion library info from dev TV"
    )
    baseline_metrics: Optional[BaselineMetrics] = Field(
        None, description="Baseline system metrics"
    )
    
    # Execution
    repro_steps_executed: List[ReproStep] = Field(
        default_factory=list, description="Steps executed during reproduction"
    )
    execution_time_seconds: float = Field(
        default=0.0, description="Total execution time"
    )
    
    # Legacy compat
    reproducible: bool = Field(
        False, description="Legacy field: True if status=REPRODUCED"
    )
    
    def model_post_init(self, __context):
        """Set legacy reproducible field based on status."""
        self.reproducible = (self.status == ReproductionStatus.REPRODUCED)


__all__ = [
    "CompanionLibInfo",
    "ReproductionStrategy",
    "ReproductionStatus",
    "ReproductionEvidence",
    "BaselineMetrics",
    "ReproStep",
    "ReproResultV2",
]
