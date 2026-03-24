"""
Log Analysis Module
===================

Quality gate, BugLayerRouter, error pattern matching, and log intelligence.

Mostly ported from mcp_server_jira_log_analyzer POC with 100+ enriched patterns.

Components:
- quality_gate.py: Stage -1 (TimeWindowFilter + StructuralParser)
- bug_layer_router.py: Stage 0 (classify bug into LOKI/HTML5/MEDIATEK/CROSS_LAYER)
- error_patterns.py: 100+ enriched error patterns with bug_layer metadata
- models.py: BugLayer enum, ErrorCategory, PipelineState
- drain_adapter.py: Log template clustering (95%+ dedup)
- timestamp_extractor.py: Multi-format timestamp extraction
- log_utils.py: Shared log parsing utilities
- correlation_engine.py: Temporal error correlation
- incident_detector.py: Error burst clustering (60s gap)
- anomaly_detector.py: Rate-spike detection (3x baseline)
- cascading_detector.py: Causal chain detection
- settings_analyzer.py: TV settings analysis
"""

__all__ = [
    "LogQualityGate",
    "BugLayerRouter",
    "BugLayer",
    "ErrorCategory",
    "PipelineState",
    "VizioSpecificDrainAdapter",
    "TimestampExtractor",
    "CorrelationEngine",
    "IncidentDetector",
    "AnomalyDetector",
    "CascadingDetector",
    "SettingsAnalyzer",
]
