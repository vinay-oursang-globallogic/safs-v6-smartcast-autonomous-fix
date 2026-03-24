"""
SAFS v6.0 - Log Intelligence Data Models

Pydantic models for Log Intelligence Agent (Stages 1-2: Log Parsing + Symbolication).
Includes:
- Log templates (Drain clustering)
- Timestamp enrichment
- Temporal correlations, incidents, anomalies
- Symbolicated frames (LOKi ASLR-corrected)
- CDP traces (HTML5 debugging)
- Kernel oops analysis (MediaTek)
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


# ==================================================================================
# DRAIN LOG TEMPLATING MODELS
# ==================================================================================


class LogTemplate(BaseModel):
    """
    Drain log template from clustering.
    Example: "User <*> logged in from <*>" from 50 similar log lines.
    """

    id: str = Field(description="Template hash ID")
    template: str = Field(description="Template string with <*> for variables")
    count: int = Field(description="Number of logs matching this template", ge=0)
    examples: list[str] = Field(
        description="Sample original log lines (max 3)", max_length=3
    )
    first_seen: Optional[datetime] = Field(
        None, description="First occurrence timestamp"
    )
    last_seen: Optional[datetime] = Field(None, description="Last occurrence timestamp")


class DrainResult(BaseModel):
    """Result from Drain log template clustering"""

    templates: list[LogTemplate] = Field(
        description="Extracted log templates sorted by count"
    )
    total_logs: int = Field(description="Total log lines processed", ge=0)
    total_templates: int = Field(description="Number of unique templates", ge=0)
    reduction_ratio: float = Field(
        description="Deduplication ratio (0-1, higher = more compression)", ge=0, le=1
    )


# ==================================================================================
# TIMESTAMP ENRICHMENT MODELS
# ==================================================================================


class TimestampFormat(str, Enum):
    """Supported timestamp formats"""

    KERNEL_UPTIME = "KERNEL_UPTIME"  # [  417.695436]
    SYSLOG = "SYSLOG"  # Dec 10 16:57:32.011815
    ANDROID_LOGCAT = "ANDROID_LOGCAT"  # 12-15 14:30:45.123
    ISO8601 = "ISO8601"  # 2025-12-11T14:30:45.123Z
    UNKNOWN = "UNKNOWN"


class EnrichedLogLine(BaseModel):
    """Log line with extracted timestamp and metadata"""

    line_number: int = Field(description="Original line number in file", ge=1)
    raw_line: str = Field(description="Original log line text")
    timestamp: Optional[datetime] = Field(
        None, description="Extracted/inferred timestamp"
    )
    timestamp_format: TimestampFormat = Field(
        description="Detected timestamp format"
    )
    severity: Optional[str] = Field(None, description="Log level (ERROR, WARN, INFO)")


# ==================================================================================
# TEMPORAL CORRELATION MODELS
# ==================================================================================


class ErrorCorrelation(BaseModel):
    """Temporal correlation between two error types"""

    error1: str = Field(description="First error type")
    error2: str = Field(description="Second error type (occurs after error1)")
    count: int = Field(description="Number of times this correlation observed", ge=1)
    avg_time_diff_seconds: float = Field(
        description="Average time between error1 and error2 (seconds)", ge=0
    )
    confidence: float = Field(
        description="Correlation confidence (0-1)", ge=0, le=1
    )


class ErrorSequence(BaseModel):
    """Sequence of errors leading to a critical event"""

    sequence: list[tuple[str, float]] = Field(
        description="List of (error_type, timestamp) tuples"
    )
    leads_to: str = Field(description="Final critical error type")
    occurrences: int = Field(
        description="Number of times this sequence observed", ge=1
    )
    confidence: float = Field(
        description="Sequence confidence (0-1)", ge=0, le=1
    )


# ==================================================================================
# INCIDENT DETECTION MODELS
# ==================================================================================


class Incident(BaseModel):
    """
    Cluster of related errors forming an incident.
    Detected via 60s gap clustering (POC SmartTVErrorAnalyzer).
    """

    incident_id: str = Field(description="Unique incident identifier")
    start_time: datetime = Field(description="Incident start timestamp")
    end_time: datetime = Field(description="Incident end timestamp")
    duration_seconds: float = Field(description="Incident duration", ge=0)
    error_count: int = Field(
        description="Total errors in this incident", ge=1
    )
    unique_error_types: set[str] = Field(
        description="Set of distinct error types in incident"
    )
    root_cause_candidates: list[str] = Field(
        description="Potential root causes ranked by heuristic"
    )
    severity: str = Field(description="Severity: LOW, MEDIUM, HIGH, CRITICAL")


# ==================================================================================
# ANOMALY DETECTION MODELS
# ==================================================================================


class Anomaly(BaseModel):
    """Anomalous error rate spike (3x baseline threshold)"""

    error_type: str = Field(description="Error type with anomalous spike")
    window_start: datetime = Field(description="Spike window start")
    window_end: datetime = Field(description="Spike window end")
    baseline_rate: float = Field(
        description="Baseline error rate (errors/minute)", ge=0
    )
    spike_rate: float = Field(
        description="Spike error rate (errors/minute)", ge=0
    )
    spike_magnitude: float = Field(
        description="Spike magnitude ratio (spike_rate / baseline_rate)", ge=1
    )


# ==================================================================================
# CASCADING FAILURE MODELS
# ==================================================================================


class CascadingFailure(BaseModel):
    """
    Causal chain of failures (POC SmartTVErrorAnalyzer cascading detection).
    Example: Companion server timeout → app launch fail → user-visible error.
    """

    chain: list[str] = Field(
        description="Failure chain (earliest to latest error types)"
    )
    start_time: datetime = Field(description="Cascade start timestamp")
    end_time: datetime = Field(description="Cascade end timestamp")
    total_duration_seconds: float = Field(
        description="Total cascade duration", ge=0
    )
    impact: str = Field(description="Impact level: LOW, MEDIUM, HIGH, CRITICAL")


# ==================================================================================
# LOKI SYMBOLICATION MODELS (NEW)
# ==================================================================================


class LoadMapEntry(BaseModel):
    """Entry from /proc/pid/maps (ASLR load address)"""

    library_name: str = Field(description="Library filename (e.g., libloki_core.so)")
    load_address: int = Field(
        description="Virtual load address (hex converted to int)", ge=0
    )
    end_address: int = Field(description="End address of mapped region", ge=0)
    permissions: str = Field(description="rwxp permissions string")


class BacktraceFrame(BaseModel):
    """Raw backtrace frame from crash log"""

    frame_number: int = Field(description="Frame index (0 = crash site)", ge=0)
    library_name: str = Field(description="Library name")
    virtual_pc: int = Field(
        description="Virtual PC address from crash log (with ASLR)", ge=0
    )
    build_id: Optional[str] = Field(
        None, description="ELF Build-ID (if available in log)"
    )


class SymbolicatedFrame(BaseModel):
    """Symbolicated backtrace frame (ASLR-corrected)"""

    frame_number: int = Field(ge=0)
    library_name: str
    virtual_pc: int = Field(ge=0)
    file_offset: Optional[int] = Field(
        None, description="File offset after ASLR correction", ge=0
    )
    function_name: Optional[str] = Field(None, description="Function name (from addr2line)")
    file_name: Optional[str] = Field(None, description="Source file path")
    line_number: Optional[int] = Field(None, description="Source line number", ge=1)
    status: str = Field(
        description="Symbolication status: OK, NO_DEBUG_ELF, ASLR_UNKNOWN, ADDR2LINE_FAIL"
    )


class LokiSymbolicationResult(BaseModel):
    """Complete LOKi symbolication result"""

    load_map: list[LoadMapEntry] = Field(description="Parsed /proc/pid/maps")
    raw_frames: list[BacktraceFrame] = Field(description="Raw backtrace frames")
    symbolicated_frames: list[SymbolicatedFrame] = Field(
        description="Symbolicated frames (may have partial results)"
    )
    symbolication_success_rate: float = Field(
        description="% of frames successfully symbolicated (0-1)", ge=0, le=1
    )


# ==================================================================================
# CDP PARSER MODELS (NEW)
# ==================================================================================


class CDPEvent(BaseModel):
    """Chrome DevTools Protocol event"""

    timestamp: datetime = Field(description="Event timestamp")
    method: str = Field(description="CDP method (e.g., 'Runtime.exceptionThrown')")
    params: dict[str, Any] = Field(
        description="Event parameters (JSON object)"
    )


class CDPException(BaseModel):
    """Parsed JavaScript exception from CDP"""

    timestamp: datetime
    exception_type: str = Field(description="Exception class (TypeError, ReferenceError)")
    message: str = Field(description="Exception message")
    stack_trace: list[str] = Field(description="JS stack trace lines")
    url: Optional[str] = Field(None, description="Script URL where exception occurred")
    line_number: Optional[int] = Field(None, ge=1)
    column_number: Optional[int] = Field(None, ge=1)


class CDPParseResult(BaseModel):
    """Result from CDP JSON trace parsing"""

    events: list[CDPEvent] = Field(description="All parsed CDP events")
    exceptions: list[CDPException] = Field(
        description="Extracted JavaScript exceptions"
    )
    console_errors: list[str] = Field(
        description="console.error() messages"
    )
    network_errors: list[str] = Field(
        description="Network request failures"
    )


# ==================================================================================
# SOURCE MAP MODELS (NEW)
# ==================================================================================


class SourceMapPosition(BaseModel):
    """Original source position from source map"""

    original_file: str = Field(description="Original source file path")
    original_line: int = Field(ge=1)
    original_column: int = Field(ge=0)
    original_name: Optional[str] = Field(None, description="Original symbol name")


class SourceMappedFrame(BaseModel):
    """JS stack frame with source map applied"""

    minified_file: str
    minified_line: int = Field(ge=1)
    minified_column: int = Field(ge=0)
    original_position: Optional[SourceMapPosition] = Field(
        None, description="Original source position (if source map available)"
    )
    status: str = Field(
        description="Mapping status: OK, NO_SOURCE_MAP, PARSE_ERROR"
    )


# ==================================================================================
# MEDIATEK KERNEL PARSER MODELS (NEW)
# ==================================================================================


class KernelOops(BaseModel):
    """Parsed Linux kernel oops/panic"""

    timestamp: datetime
    oops_type: str = Field(description="Oops type: NULL_DEREF, PAGE_FAULT, PANIC, etc.")
    faulting_address: Optional[int] = Field(
        None, description="Fault address (hex converted to int)"
    )
    instruction_pointer: int = Field(description="PC/IP register", ge=0)
    call_trace: list[str] = Field(description="Kernel call trace")
    tainted: bool = Field(
        description="Kernel tainted flag (proprietary modules loaded)"
    )
    subsystem: Optional[str] = Field(
        None,
        description="MTK subsystem: VDEC, TRUSTZONE, MALI_GPU, HDMI, IR_INPUT, etc.",
    )


class MediaTekKernelResult(BaseModel):
    """MediaTek kernel log analysis result"""

    oops_list: list[KernelOops] = Field(description="Detected kernel oopses")
    hardware_errors: list[str] = Field(
        description="Hardware-level errors (auto-escalate to hw_triage)"
    )
    subsystem_classification: dict[str, int] = Field(
        description="Count of errors by MTK subsystem"
    )


# ==================================================================================
# LOG INTELLIGENCE AGENT RESULT (COMBINED)
# ==================================================================================


class LogAnalysisResult(BaseModel):
    """
    Complete result from LogIntelligenceAgent.analyze().
    Combines POC analysis engines + new symbolication/parsing.
    """

    # Drain clustering
    drain: DrainResult = Field(description="Log template clustering result")

    # Timestamp enrichment
    enriched_lines: list[EnrichedLogLine] = Field(
        description="Logs with extracted timestamps"
    )

    # POC SmartTVErrorAnalyzer analysis engines
    correlations: list[ErrorCorrelation] = Field(
        description="Temporal error correlations"
    )
    incidents: list[Incident] = Field(description="Error burst incidents")
    anomalies: list[Anomaly] = Field(description="Error rate anomalies")
    cascading_failures: list[CascadingFailure] = Field(
        description="Causal failure chains"
    )
    heuristic_root_causes: list[str] = Field(
        description="Heuristic root cause candidates (POC logic)"
    )

    # Layer-specific results (conditional)
    loki_symbolication: Optional[LokiSymbolicationResult] = Field(
        None, description="LOKi symbolication (if BugLayer=LOKI)"
    )
    cdp_analysis: Optional[CDPParseResult] = Field(
        None, description="CDP analysis (if BugLayer=HTML5)"
    )
    source_mapped_frames: Optional[list[SourceMappedFrame]] = Field(
        None, description="Source-mapped JS frames (if CDP traces present)"
    )
    mediatek_analysis: Optional[MediaTekKernelResult] = Field(
        None, description="Kernel analysis (if BugLayer=MEDIATEK)"
    )
