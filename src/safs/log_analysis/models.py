"""
SAFS v6.0 — Pydantic Data Models

This module defines all data models used throughout the SAFS pipeline.
Ported from mcp_server_jira_log_analyzer/interfaces.py and enhanced with SAFS v6.0 requirements.

Models:
- Enums: BugLayer, ErrorCategory, ConfidenceRouting, EventType, MistakeSeverity, FixStrategy
- Core Models: PipelineState, FixCandidate, ValidationResult, QualityResult, BugLayerResult
- Analysis Models: LogAnalysisResult, RootCauseResult, ContextResult, ReproResult
- Data Models: LogLine, LogFile, Attachment, JiraTicket, Report, Event

All models are Pydantic v2 BaseModel with full JSON serialization support.
"""

import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ============================================================================
# PART 1: ENUMS
# ============================================================================


class BugLayer(str, Enum):
    """
    Three-layer Vizio SmartCast stack classification.
    
    Routing Logic:
    - LOKI: C++ native crashes in /3rd/loki/bin/loki
    - HTML5: JavaScript errors in Chromium streaming apps
    - MEDIATEK: Hardware/firmware issues (auto-escalate, no fix generated)
    - CROSS_LAYER: Issues spanning LOKi + HTML5 (e.g., WatchFree+ deeplink loss)
    - UNKNOWN: Insufficient signal for classification
    """
    LOKI = "LOKI"
    HTML5 = "HTML5"
    MEDIATEK = "MEDIATEK"
    CROSS_LAYER = "CROSS_LAYER"
    UNKNOWN = "UNKNOWN"


class ErrorCategory(str, Enum):
    """
    27 error categories from SAFS v6.0 Master Prompt Part Two.
    
    Categories are divided into:
    - LOKi Native C++ (8 categories: LOKI_*)
    - HTML5 Streaming Apps (13 categories: COMPANION_LIB_*, JS_*, EME_*, etc.)
    - MediaTek Driver (6 categories: MTK_*, auto-escalate)
    """
    # LOKi Native C++ Categories (8)
    LOKI_SEGFAULT_NULL_DEREF = "LOKI_SEGFAULT_NULL_DEREF"
    LOKI_MEMORY_CORRUPTION = "LOKI_MEMORY_CORRUPTION"
    LOKI_RACE_CONDITION = "LOKI_RACE_CONDITION"
    LOKI_APP_LAUNCH_FAILURE = "LOKI_APP_LAUNCH_FAILURE"
    LOKI_IR_ROUTING_FAILURE = "LOKI_IR_ROUTING_FAILURE"
    LOKI_COMPANION_SERVER_DEADLOCK = "LOKI_COMPANION_SERVER_DEADLOCK"
    LOKI_EPG_PARSE_ERROR = "LOKI_EPG_PARSE_ERROR"
    LOKI_OTA_UPDATE_FAILURE = "LOKI_OTA_UPDATE_FAILURE"
    
    # HTML5 Streaming App Categories (13)
    COMPANION_LIB_TIMING = "COMPANION_LIB_TIMING"
    JS_HEAP_OOM = "JS_HEAP_OOM"
    EME_DRM_FAILURE = "EME_DRM_FAILURE"
    KEYDOWN_NOT_FIRED = "KEYDOWN_NOT_FIRED"
    FETCH_NETWORK_TIMEOUT = "FETCH_NETWORK_TIMEOUT"
    SHAKA_ERROR_3016 = "SHAKA_ERROR_3016"
    NETFLIX_MSL_TIMEOUT = "NETFLIX_MSL_TIMEOUT"
    AMAZON_DASH_MANIFEST = "AMAZON_DASH_MANIFEST"
    HULU_AD_MSE_BREAK = "HULU_AD_MSE_BREAK"
    WATCHFREE_DEEPLINK_LOSS = "WATCHFREE_DEEPLINK_LOSS"
    CHROMIUM_VERSION_COMPAT = "CHROMIUM_VERSION_COMPAT"
    FOCUS_MANAGEMENT = "FOCUS_MANAGEMENT"
    MEMORY_LEAK_EVENT_LISTENER = "MEMORY_LEAK_EVENT_LISTENER"
    
    # MediaTek Driver Categories (6) — Auto-Escalate
    MTK_VDEC_CRASH = "MTK_VDEC_CRASH"
    MTK_MALI_GPU_HANG = "MTK_MALI_GPU_HANG"
    MTK_HDCP_FAILURE = "MTK_HDCP_FAILURE"
    MTK_TEE_WIDEVINE = "MTK_TEE_WIDEVINE"
    MTK_ADSP_CRASH = "MTK_ADSP_CRASH"
    MTK_MMC_IO_ERROR = "MTK_MMC_IO_ERROR"


class ConfidenceRouting(str, Enum):
    """
    Routing decision based on fix confidence score.
    
    Thresholds (from Master Prompt):
    - AUTO_PR: confidence >= 0.85 (auto-merge if tests pass)
    - PR_WITH_REVIEW: 0.60 <= confidence < 0.85 (requires human review)
    - ANALYSIS_ONLY: 0.40 <= confidence < 0.60 (post analysis, no PR)
    - ESCALATE_HUMAN: confidence < 0.40 (insufficient data, escalate)
    """
    AUTO_PR = "AUTO_PR"
    PR_WITH_REVIEW = "PR_WITH_REVIEW"
    ANALYSIS_ONLY = "ANALYSIS_ONLY"
    ESCALATE_HUMAN = "ESCALATE_HUMAN"


class EventType(str, Enum):
    """
    Event types extracted from logs (ported from POC interfaces.py).
    """
    LOGSTART = "LOGSTART"
    LOGEND = "LOGEND"
    SUSPEND = "SUSPEND"
    KEYPRESS = "KEYPRESS"
    ERIS = "ERIS"


class MistakeSeverity(str, Enum):
    """
    Severity classification for bug impact.
    """
    CRITICAL = "CRITICAL"  # Crash, data loss, security
    HIGH = "HIGH"  # Feature unusable, severe UX degradation
    MEDIUM = "MEDIUM"  # Intermittent failure, moderate UX issue
    LOW = "LOW"  # Minor cosmetic, edge case
    INFO = "INFO"  # Not a bug, informational


class FixStrategy(str, Enum):
    """
    Fix generation strategy (determined by error category).
    """
    NULL_CHECK = "NULL_CHECK"
    SMART_POINTER = "SMART_POINTER"
    MUTEX_GUARD = "MUTEX_GUARD"
    RETRY_WITH_BACKOFF = "RETRY_WITH_BACKOFF"
    EVENT_LISTENER_CLEANUP = "EVENT_LISTENER_CLEANUP"
    POLYFILL = "POLYFILL"
    CONFIG_UPDATE = "CONFIG_UPDATE"
    CROSS_LAYER_FIX = "CROSS_LAYER_FIX"
    AUTO_ESCALATE = "AUTO_ESCALATE"
    UNKNOWN = "UNKNOWN"


# ============================================================================
# PART 2: LOG DATA MODELS (Ported from POC)
# ============================================================================


class LogLine(BaseModel):
    """
    Single log line with extracted timestamps.
    Ported from POC interfaces.py (dataclass) -> Pydantic.
    """
    log_line: str = Field(..., description="Raw log line text")
    log_prefix_timestamp: Optional[datetime] = Field(
        None, description="Timestamp at the beginning of the log line"
    )
    log_line_timestamp: Optional[datetime] = Field(
        None, description="Secondary timestamp within the log line message"
    )
    wall_clock_timestamp: Optional[datetime] = Field(
        None, description="Wall clock timestamp (if available)"
    )


class Event(BaseModel):
    """
    Extracted event from log (KEYPRESS, ERIS, SUSPEND, etc.).
    Simplified from POC (removed circular Log reference).
    """
    event_type: EventType = Field(..., description="Type of event")
    log_line: LogLine = Field(..., description="Log line containing the event")
    event_specific_data: Optional[Dict[str, Any]] = Field(
        None, description="Additional event-specific metadata"
    )


class Report(BaseModel):
    """
    Analysis report generated by a specific analyzer.
    Ported from POC interfaces.py (dataclass) -> Pydantic.
    """
    analyzer: str = Field(..., description="Name of analyzer that generated report")
    title: str = Field(..., description="Report title")
    report: str = Field(..., description="Report content (Markdown)")
    events: int = Field(default=0, description="Number of events analyzed")
    priority: int = Field(default=100, description="Priority score (lower = higher priority)")
    plugin: Optional[Dict[str, Any]] = Field(None, description="Plugin-specific metadata")
    extended: Optional[Dict[str, Any]] = Field(None, description="Extended metadata")
    lines_data: List[LogLine] = Field(default_factory=list, description="Related log lines")


class LogFile(BaseModel):
    """
    Represents a single log file from Jira attachment.
    Enhanced from POC's Log dataclass.
    """
    path_to_file: str = Field(..., description="Absolute path to log file on disk")
    path_from_log_root: str = Field(..., description="Relative path within attachment archive")
    attachment_filename: str = Field(..., description="Original attachment filename")
    from_archive: bool = Field(default=False, description="Whether extracted from ZIP/tar")
    reports: List[Report] = Field(default_factory=list, description="Generated reports")
    timestamped_log_lines: List[LogLine] = Field(
        default_factory=list, description="Log lines with timestamps extracted"
    )
    events: List[Event] = Field(default_factory=list, description="Extracted events")


class Attachment(BaseModel):
    """
    Jira attachment metadata and downloaded file path.
    """
    id: str = Field(..., description="Jira attachment ID")
    filename: str = Field(..., description="Attachment filename")
    size: int = Field(..., description="File size in bytes")
    mime_type: str = Field(..., description="MIME type")
    content_url: str = Field(..., description="Jira download URL")
    path_to_file: Optional[str] = Field(None, description="Local downloaded file path")
    log_files: List[LogFile] = Field(default_factory=list, description="Extracted log files")
    already_processed: bool = Field(default=False, description="Whether already processed")


class JiraTicket(BaseModel):
    """
    Jira ticket metadata and attachments.
    Enhanced from POC's Issue dataclass.
    """
    key: str = Field(..., description="Jira ticket key (e.g., TVPF-12345)")
    summary: str = Field(default="", description="Ticket summary")
    description: str = Field(default="", description="Ticket description")
    attachments: List[Attachment] = Field(default_factory=list, description="Ticket attachments")
    issue_directory: Optional[str] = Field(None, description="Local working directory")
    analyzers_run: Set[str] = Field(default_factory=set, description="Analyzers already run")
    
    # Phase 9: Bug Reproduction fields
    priority: Optional[str] = Field(None, description="Bug priority (P0-P4)")
    firmware_version: Optional[str] = Field(None, description="Firmware version mentioned in ticket")
    streaming_app: Optional[str] = Field(None, description="Affected streaming application")
    affected_app: Optional[str] = Field(None, description="Alternative field for affected app")
    repro_steps: List[str] = Field(default_factory=list, description="Reproduction steps from ticket")
    reproduction_steps: List[str] = Field(default_factory=list, description="Alternative field for repro steps")


# ============================================================================
# PART 3: PIPELINE STAGE RESULTS
# ============================================================================


class QualityResult(BaseModel):
    """
    Stage -1: Log Quality Gate output.
    """
    passed: bool = Field(..., description="Whether quality gate passed")
    score: float = Field(..., ge=0.0, le=1.0, description="Quality score (0.0-1.0)")
    reasons: List[str] = Field(
        default_factory=list, description="Reasons for failure/low score"
    )
    log_file_count: int = Field(default=0, description="Number of log files")
    total_lines: int = Field(default=0, description="Total log lines")
    timestamp_coverage: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Fraction of lines with timestamps"
    )
    filtered_logs: List[str] = Field(
        default_factory=list, description="Filtered log lines that passed quality gate"
    )


class BugLayerResult(BaseModel):
    """
    Stage 0: BugLayerRouter classification output.
    """
    layer: BugLayer = Field(..., description="Classified bug layer")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Classification confidence")
    layer_scores: Dict[BugLayer, float] = Field(
        default_factory=dict, description="Scores for all layers"
    )
    matched_patterns: List[str] = Field(
        default_factory=list, description="Pattern IDs that matched"
    )


class LogAnalysisResult(BaseModel):
    """
    Stage 1: Log Intelligence output.
    Combines Drain3 parsing, TF-IDF scoring, MinHash deduplication.
    """
    correlations: List[Dict[str, Any]] = Field(
        default_factory=list, description="Correlated log sequences"
    )
    incidents: List[Dict[str, Any]] = Field(
        default_factory=list, description="Detected incidents"
    )
    anomalies: List[Dict[str, Any]] = Field(
        default_factory=list, description="Detected anomalies"
    )
    drain_clusters: List[Dict[str, Any]] = Field(
        default_factory=list, description="Drain3 log clusters"
    )
    tfidf_keywords: List[str] = Field(
        default_factory=list, description="TF-IDF extracted keywords"
    )
    duplicate_groups: List[List[str]] = Field(
        default_factory=list, description="MinHash duplicate groups"
    )


class RootCauseResult(BaseModel):
    """
    Stage 2: Root Cause Analysis output.
    Claude Haiku synthesis with confidence score.
    """
    root_cause: str = Field(..., description="Root cause analysis (Markdown)")
    confidence: float = Field(..., ge=0.0, le=1.0, description="RCA confidence")
    error_category: ErrorCategory = Field(..., description="Classified error category")
    severity: MistakeSeverity = Field(..., description="Bug severity")
    affected_files: List[str] = Field(
        default_factory=list, description="Files likely involved in root cause"
    )


class ContextResult(BaseModel):
    """
    Stage 5: Context Assembly output (from ContextBuilderAgent).
    Combines four-path retrieval (GitHub/GitLab/Bitbucket, Code-Index MCP, Qdrant, on-device registry).
    Enhanced with similar fixes and known mistakes for FixGenerator.
    """
    github_files: List[Dict[str, Any]] = Field(
        default_factory=list, description="Files retrieved from GitHub/GitLab/Bitbucket"
    )
    code_index_results: List[Dict[str, Any]] = Field(
        default_factory=list, description="Code-Index MCP semantic search results"
    )
    qdrant_results: List[Dict[str, Any]] = Field(
        default_factory=list, description="Qdrant vector search results"
    )
    registry_data: Optional[Dict[str, Any]] = Field(
        None, description="On-device registry data (if vizio-ssh available)"
    )
    context_summary: str = Field(default="", description="Assembled context summary")
    similar_fixes: List[Dict[str, Any]] = Field(
        default_factory=list, description="Historical similar fixes from PATH C (institutional memory)"
    )
    known_mistakes: List[Dict[str, Any]] = Field(
        default_factory=list, description="Known anti-patterns to avoid from PATH C"
    )
    primary_locations: List[Dict[str, Any]] = Field(
        default_factory=list, description="Primary code locations with confidence scores"
    )
    code_locations: List[Dict[str, Any]] = Field(
        default_factory=list, description="Alias for primary_locations for backward compatibility"
    )


class ReproResult(BaseModel):
    """
    Stage 4: Bug Reproduction Evidence.
    """
    reproducible: bool = Field(..., description="Whether bug is reproducible")
    repro_steps: List[str] = Field(default_factory=list, description="Reproduction steps")
    repro_rate: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Reproduction rate (0.0-1.0)"
    )
    repro_logs: List[str] = Field(
        default_factory=list, description="Log snippets showing reproduction"
    )


class ValidationResult(BaseModel):
    """
    Tri-path validation result (α: QEMU, β: Playwright, γ: On-device).
    """
    path_alpha_qemu: Optional[Dict[str, Any]] = Field(
        None, description="QEMU ARM validation (ASan/TSan)"
    )
    path_beta_playwright: Optional[Dict[str, Any]] = Field(
        None, description="Playwright headless validation"
    )
    path_gamma_ondevice: Optional[Dict[str, Any]] = Field(
        None, description="On-device validation via vizio-mcp"
    )
    overall_passed: bool = Field(..., description="Whether all paths passed")
    failure_reasons: List[str] = Field(
        default_factory=list, description="Reasons for validation failures"
    )


class FixCandidate(BaseModel):
    """
    Generated fix candidate with strategy and file changes.
    """
    fix_id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Unique fix ID")
    strategy: FixStrategy = Field(..., description="Fix strategy")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Fix confidence")
    routing: ConfidenceRouting = Field(..., description="Routing decision")
    file_changes: List[Dict[str, Any]] = Field(
        default_factory=list, description="File changes (diffs)"
    )
    diff: str = Field(default="", description="Unified diff")
    explanation: str = Field(default="", description="Fix explanation (Markdown)")
    summary: str = Field(default="", description="Short summary of the fix")
    
    # PR creation fields (Phase 14)
    target_repo: str = Field(default="vizio/SmartCast", description="Target repository for PR")
    target_branch: str = Field(default="main", description="Target branch for PR")
    
    # CROSS_LAYER support
    has_secondary_fix: bool = Field(default=False, description="Whether this has a secondary fix for CROSS_LAYER")
    secondary_repo: Optional[str] = Field(None, description="Secondary repository for CROSS_LAYER fixes")
    secondary_file_changes: List[Dict[str, Any]] = Field(
        default_factory=list, description="Secondary file changes for CROSS_LAYER"
    )
    secondary_diff: str = Field(default="", description="Secondary diff for CROSS_LAYER")
    secondary_summary: str = Field(default="", description="Secondary fix summary for CROSS_LAYER")
    
    # Validation fields
    validation_result: Optional[ValidationResult] = Field(
        None, description="Validation result (if run)"
    )
    validation_passed: Optional[bool] = Field(
        None, description="Whether validation passed (Stage 7)"
    )
    validation_evidence: Optional[str] = Field(
        None, description="Validation evidence/details (Stage 7)"
    )
    ensemble_confidence: Optional[float] = Field(
        None, ge=0.0, le=1.0, description="Confidence score from ensemble (Stage 7.5)"
    )
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), description="Timestamp of fix generation"
    )


# ============================================================================
# PART 4: PIPELINE STATE (Master Model)
# ============================================================================


class PipelineState(BaseModel):
    """
    Complete pipeline execution state.
    Used by LangGraph orchestrator to track state across all stages.
    
    Stages:
    - Stage -1: Quality Gate
    - Stage 0: BugLayerRouter
    - Stage 1: Log Intelligence
    - Stage 2: Root Cause Analysis
    - Stage 3: Context Assembly
    - Stage 4: Bug Reproduction
    - Stage 5: Fix Generation
    - Stage 6: Tri-Path Validation
    - Stage 7: PR Creation
    - Stage 8: Telemetry
    """
    # Input
    ticket: JiraTicket = Field(..., description="Jira ticket metadata")
    
    # Stage Results
    quality_result: Optional[QualityResult] = Field(None, description="Stage -1 result")
    buglayer_result: Optional[BugLayerResult] = Field(None, description="Stage 0 result")
    # Typed as Any because the runtime value is safs.log_intelligence.models.LogAnalysisResult
    # (the richer model with drain, enriched_lines, cascading_failures, etc.).  Typing it as
    # the local simplified LogAnalysisResult would silently strip those fields via Pydantic
    # coercion.  Downstream consumers (RootCauseAgent) receive the full object directly from
    # the orchestrator, not via state, so Any here is safe.
    log_analysis_result: Optional[Any] = Field(None, description="Stage 1 result (safs.log_intelligence.models.LogAnalysisResult)")
    root_cause_result: Optional[RootCauseResult] = Field(None, description="Stage 2 result")
    context_result: Optional[ContextResult] = Field(None, description="Stage 3 result")
    repro_result: Optional["ReproResultV2"] = Field(None, description="Stage 5.5 result (NEW v6.0 format)")
    fix_candidates: List[FixCandidate] = Field(
        default_factory=list, description="Stage 5 result (multiple candidates)"
    )
    validation_result: Optional[ValidationResult] = Field(None, description="Stage 6 result (deprecated)")
    validation_results: Optional[List["CandidateValidationResult"]] = Field(
        None, description="Stage 7 tri-path validation results (one per candidate)"
    )
    pr_url: Optional[str] = Field(None, description="Stage 7 result (PR URL)")
    
    # Metadata
    pipeline_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()), description="Unique pipeline execution ID"
    )
    started_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), description="Pipeline start timestamp"
    )
    completed_at: Optional[datetime] = Field(None, description="Pipeline completion timestamp")
    current_stage: str = Field(default="INIT", description="Current pipeline stage")
    ticket_priority: str = Field(default="P2", description="Ticket priority for rate limiting (P0/P1/P2/P3)")
    errors: List[str] = Field(default_factory=list, description="Errors encountered")


# ============================================================================
# DEFERRED IMPORTS (to avoid circular dependencies at module load time)
# ============================================================================

def _rebuild_forward_refs():
    """Rebuild models with forward references after all imports are complete."""
    try:
        from safs.reproduction.models import ReproResultV2
        from safs.validation.models import CandidateValidationResult
        PipelineState.model_rebuild()
    except ImportError:
        # reproduction.models or validation.models not yet available (e.g., during initial import)
        pass

# Call rebuild at module load time
_rebuild_forward_refs()
