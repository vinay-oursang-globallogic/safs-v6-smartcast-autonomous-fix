"""
SAFS v6.0 - POC Component Adapters

Adapters that wrap log-analysis engines for seamless integration with SAFS v6.0 data models.

These adapters:
1. Convert engine outputs to SAFS Pydantic models
2. Handle engine initialization with SAFS config
3. Provide unified interface for LogIntelligenceAgent

Engines are provided by the standalone_engines module (self-contained, no external POC dependency).
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Import self-contained engine implementations
from .standalone_engines import (
    ContextAnalyzer,
    EnhancedTimestampExtractor,
    SimplifiedDrainParser,
    SmartTVErrorAnalyzer,
    ErrorCorrelation as POCErrorCorrelation,
    Incident as POCIncident,
    Anomaly as POCAnomaly,
    LogTemplate as POCLogTemplate,
)

# Prefer the full VizioSpecificDrainAdapter when drain3 is installed
try:
    from safs.log_analysis.drain_adapter import VizioSpecificDrainAdapter as _VizioAdapter
    _VIZIO_ADAPTER_AVAILABLE = True
except ImportError:
    _VizioAdapter = None
    _VIZIO_ADAPTER_AVAILABLE = False

# SAFS models
from .models import (
    Anomaly,
    CascadingFailure,
    DrainResult,
    EnrichedLogLine,
    ErrorCorrelation,
    Incident,
    LogTemplate,
    TimestampFormat,
)


# ==================================================================================
# DRAIN PARSER ADAPTER
# ==================================================================================


class DrainParserAdapter:
    """Adapter for log template clustering.

    Uses ``VizioSpecificDrainAdapter`` (drain3-backed) when drain3 is installed,
    falling back to ``SimplifiedDrainParser`` otherwise.
    """

    def __init__(
        self, similarity_threshold: float = 0.5, max_examples: int = 3
    ):
        self._use_vizio = _VIZIO_ADAPTER_AVAILABLE
        if self._use_vizio:
            self._vizio_adapter = _VizioAdapter(sim_threshold=similarity_threshold)
        else:
            self.parser = SimplifiedDrainParser(
                similarity_threshold=similarity_threshold,
                max_examples=max_examples,
            )
        self._max_examples = max_examples

    def process_logs(self, log_lines: list[str]) -> DrainResult:
        """
        Process log lines through Drain clustering.

        Args:
            log_lines: List of raw log lines

        Returns:
            DrainResult with templates and statistics
        """
        if self._use_vizio:
            # VizioSpecificDrainAdapter returns safs.log_analysis.drain_adapter.DrainResult
            # which uses different field names — convert to log_intelligence DrainResult
            vizio_result = self._vizio_adapter.process_logs(log_lines)
            templates = [
                LogTemplate(
                    id=str(t.template_id),
                    template=t.template_str,
                    count=t.cluster_size,
                    examples=t.sample_params[: self._max_examples],
                    first_seen=None,
                    last_seen=None,
                )
                for t in vizio_result.templates
            ]
            return DrainResult(
                templates=templates,
                total_logs=len(log_lines),
                total_templates=len(templates),
                reduction_ratio=vizio_result.reduction_ratio,
            )

        # Fallback: use SimplifiedDrainParser
        # Add logs to parser
        for line in log_lines:
            self.parser.add_log(line.strip())

        # Get templates from POC parser
        poc_templates = self.parser.get_templates()

        # Convert to SAFS models
        templates = []
        for poc_tmpl in poc_templates:
            template = LogTemplate(
                id=poc_tmpl.id,
                template=poc_tmpl.template,
                count=poc_tmpl.count,
                examples=poc_tmpl.examples[: self.parser.max_examples],
                first_seen=None,  # POC doesn't track first_seen
                last_seen=None,  # POC doesn't track last_seen
            )
            templates.append(template)

        # Sort by count descending
        templates.sort(key=lambda t: t.count, reverse=True)

        # Calculate reduction ratio
        reduction_ratio = self.parser.get_reduction_ratio()

        return DrainResult(
            templates=templates,
            total_logs=self.parser.total_logs,
            total_templates=len(templates),
            reduction_ratio=reduction_ratio,
        )


# ==================================================================================
# TIMESTAMP EXTRACTOR ADAPTER
# ==================================================================================


class TimestampExtractorAdapter:
    """Adapter for POC EnhancedTimestampExtractor"""

    def __init__(self):
        self.extractor = EnhancedTimestampExtractor()

    def enrich_logs(
        self, log_lines: list[str], log_file_path: Optional[Path] = None
    ) -> list[EnrichedLogLine]:
        """
        Extract timestamps from log lines.

        Args:
            log_lines: List of raw log lines
            log_file_path: Optional path to source log file (for kernel uptime conversion)

        Returns:
            List of EnrichedLogLine with extracted timestamps
        """
        enriched = []
        log_path_str = str(log_file_path) if log_file_path else None

        for line_number, line in enumerate(log_lines, start=1):
            # Extract timestamp using POC extractor
            timestamp = self.extractor.extract_timestamp_from_line(
                line, log_path_str
            )

            # Detect format
            timestamp_format = self._detect_format(line)

            # Extract severity (simple regex)
            severity = self._extract_severity(line)

            enriched_line = EnrichedLogLine(
                line_number=line_number,
                raw_line=line,
                timestamp=timestamp,
                timestamp_format=timestamp_format,
                severity=severity,
            )
            enriched.append(enriched_line)

        return enriched

    def _detect_format(self, line: str) -> TimestampFormat:
        """Detect timestamp format from line"""
        if "[  " in line and "]" in line and "." in line:
            return TimestampFormat.KERNEL_UPTIME
        elif "T" in line and "Z" in line:
            return TimestampFormat.ISO8601
        elif "-" in line and ":" in line and "." in line:
            # Could be logcat (MM-DD) or syslog (YYYY-MM-DD)
            if line.strip()[:2].isdigit() and line.strip()[2] == "-":
                # Starts with digit-digit-dash
                first_part = line.strip().split()[0]
                if first_part.count("-") == 1:
                    return TimestampFormat.ANDROID_LOGCAT
            return TimestampFormat.SYSLOG
        else:
            return TimestampFormat.UNKNOWN

    def _extract_severity(self, line: str) -> Optional[str]:
        """Extract log severity (ERROR, WARN, INFO, DEBUG)"""
        line_upper = line.upper()
        for severity in ["ERROR", "ERR", "WARN", "WARNING", "INFO", "DEBUG"]:
            if severity in line_upper:
                return severity
        return None


# ==================================================================================
# SMART TV ERROR ANALYZER ADAPTER
# ==================================================================================


class SmartTVErrorAnalyzerAdapter:
    """Adapter for POC SmartTVErrorAnalyzer (5 analysis engines)"""

    def __init__(self, context_keywords: Optional[list[str]] = None):
        """
        Initialize analyzer with context keywords from Jira ticket.

        Args:
            context_keywords: Keywords extracted from Jira description (e.g., ["freeze", "netflix"])
        """
        self.context_keywords = context_keywords or []
        # POC SmartTVErrorAnalyzer uses ticket_description, not context_keywords directly
        # We'll join keywords as a pseudo-description or leave empty
        ticket_description = " ".join(self.context_keywords) if self.context_keywords else ""
        self.analyzer = SmartTVErrorAnalyzer(
            ticket_description=ticket_description,
            enable_advanced_algorithms=True,
        )

    def analyze(
        self, enriched_lines: list[EnrichedLogLine]
    ) -> tuple[
        list[ErrorCorrelation],
        list[Incident],
        list[Anomaly],
        list[CascadingFailure],
        list[str],
    ]:
        """
        Run POC analysis engines.

        Args:
            enriched_lines: Timestamped log lines

        Returns:
            Tuple of (correlations, incidents, anomalies, cascading_failures, heuristic_root_causes)
        """
        # Convert SAFS enriched lines to POC format
        # POC expects list of strings with timestamps
        poc_lines = [line.raw_line for line in enriched_lines]

        # Run POC analysis
        # Note: POC methods are internal (_method_name) but we're adapting them
        try:
            # Correlations
            poc_correlations = self._analyze_correlations_wrapper(poc_lines)
            correlations = [
                ErrorCorrelation(
                    error1=c.error1,
                    error2=c.error2,
                    count=c.count,
                    avg_time_diff_seconds=c.avg_time_diff,
                    confidence=c.confidence,
                )
                for c in poc_correlations
            ]

            # Incidents
            poc_incidents = self._detect_incidents_wrapper(enriched_lines)
            incidents = [
                Incident(
                    incident_id=i.incident_id,
                    start_time=datetime.fromtimestamp(i.start_time, tz=timezone.utc),
                    end_time=datetime.fromtimestamp(i.end_time, tz=timezone.utc),
                    duration_seconds=i.duration,
                    error_count=i.error_count,
                    unique_error_types=i.unique_error_types,
                    root_cause_candidates=i.root_cause_candidates,
                    severity=i.severity,
                )
                for i in poc_incidents
            ]

            # Anomalies
            poc_anomalies = self._detect_anomalies_wrapper(enriched_lines)
            anomalies = [
                Anomaly(
                    error_type=a.error_type,
                    window_start=datetime.fromtimestamp(a.window_start, tz=timezone.utc),
                    window_end=datetime.fromtimestamp(a.window_end, tz=timezone.utc),
                    baseline_rate=a.baseline_rate,
                    spike_rate=a.spike_rate,
                    spike_magnitude=a.spike_magnitude,
                )
                for a in poc_anomalies
            ]

            # Cascading failures
            poc_cascading = self._detect_cascading_failures_wrapper(enriched_lines)
            cascading_failures = self._convert_cascading(poc_cascading)

            # Heuristic root causes
            heuristic_root_causes = self._infer_root_causes_wrapper(
                correlations, incidents, cascading_failures
            )

            return (
                correlations,
                incidents,
                anomalies,
                cascading_failures,
                heuristic_root_causes,
            )

        except Exception as e:
            # Graceful degradation if POC analysis fails
            return ([], [], [], [], [])

    def _analyze_correlations_wrapper(self, poc_lines: list[str]) -> list[POCErrorCorrelation]:
        """
        Wrapper for POC _analyze_correlations (private method).
        If not accessible, returns empty list.
        """
        # Check if POC analyzer has the method
        if hasattr(self.analyzer, "_analyze_correlations"):
            return self.analyzer._analyze_correlations(poc_lines)
        return []

    def _detect_incidents_wrapper(
        self, enriched_lines: list[EnrichedLogLine]
    ) -> list[POCIncident]:
        """Wrapper for POC _detect_incidents"""
        if hasattr(self.analyzer, "_detect_incidents"):
            # Convert to POC format (list of logs with timestamps)
            # POC expects logs to have 'timestamp' attribute
            # For now, use enriched_lines directly and hope POC handles it
            # Or we need to create POC-compatible objects
            # Let's create simple objects with required attributes
            class POCLog:
                def __init__(self, line: str, timestamp: Optional[datetime]):
                    self.line = line
                    self.timestamp = timestamp.timestamp() if timestamp else None

            poc_logs = [
                POCLog(line.raw_line, line.timestamp) for line in enriched_lines
            ]
            return self.analyzer._detect_incidents(poc_logs)
        return []

    def _detect_anomalies_wrapper(
        self, enriched_lines: list[EnrichedLogLine]
    ) -> list[POCAnomaly]:
        """Wrapper for POC _detect_anomalies"""
        if hasattr(self.analyzer, "_detect_anomalies"):
            class POCLog:
                def __init__(self, line: str, timestamp: Optional[datetime]):
                    self.line = line
                    self.timestamp = timestamp.timestamp() if timestamp else None

            poc_logs = [
                POCLog(line.raw_line, line.timestamp) for line in enriched_lines
            ]
            return self.analyzer._detect_anomalies(poc_logs)
        return []

    def _detect_cascading_failures_wrapper(
        self, enriched_lines: list[EnrichedLogLine]
    ) -> list[Any]:
        """Wrapper for POC _detect_cascading_failures"""
        if hasattr(self.analyzer, "_detect_cascading_failures"):
            class POCLog:
                def __init__(self, line: str, timestamp: Optional[datetime]):
                    self.line = line
                    self.timestamp = timestamp.timestamp() if timestamp else None

            poc_logs = [
                POCLog(line.raw_line, line.timestamp) for line in enriched_lines
            ]
            return self.analyzer._detect_cascading_failures(poc_logs)
        return []

    def _infer_root_causes_wrapper(
        self,
        correlations: list[ErrorCorrelation],
        incidents: list[Incident],
        cascading_failures: list[CascadingFailure],
    ) -> list[str]:
        """Wrapper for POC _infer_root_causes"""
        if hasattr(self.analyzer, "_infer_root_causes"):
            # Convert SAFS models back to POC format for root cause inference
            # POC expects POC-format inputs
            # For simplicity, let's extract error types and descriptions
            # and pass to heuristic logic
            # This is a simplified wrapper - actual POC may need different format
            return self.analyzer._infer_root_causes(
                correlations, incidents, cascading_failures
            )
        return []

    def _convert_cascading(self, poc_cascading: list[Any]) -> list[CascadingFailure]:
        """Convert POC cascading failures to SAFS models"""
        cascading_failures = []
        for poc_cascade in poc_cascading:
            # POC format may vary - adjust as needed
            # Assuming POC has: chain, start_time, end_time, impact
            if hasattr(poc_cascade, "chain"):
                cascading_failures.append(
                    CascadingFailure(
                        chain=poc_cascade.chain,
                        start_time=datetime.fromtimestamp(
                            poc_cascade.start_time, tz=timezone.utc
                        ),
                        end_time=datetime.fromtimestamp(
                            poc_cascade.end_time, tz=timezone.utc
                        ),
                        total_duration_seconds=poc_cascade.end_time
                        - poc_cascade.start_time,
                        impact=poc_cascade.impact
                        if hasattr(poc_cascade, "impact")
                        else "MEDIUM",
                    )
                )
        return cascading_failures


# ==================================================================================
# CONTEXT ANALYZER ADAPTER
# ==================================================================================


class ContextAnalyzerAdapter:
    """Adapter for POC ContextAnalyzer (keyword extraction from Jira description)"""

    def __init__(self):
        self.analyzer = ContextAnalyzer()

    def extract_keywords(self, jira_description: str) -> list[str]:
        """
        Extract technical keywords from Jira ticket description.

        Args:
            jira_description: Jira ticket description text

        Returns:
            List of technical keywords (e.g., ["segfault", "null", "loki"])
        """
        # POC ContextAnalyzer has CONTEXT_KEYWORDS mapping
        # Example: "freeze" -> ["deadlock", "hang", "timeout"]
        keywords = []
        desc_lower = jira_description.lower()

        for user_term, tech_terms in self.analyzer.CONTEXT_KEYWORDS.items():
            if user_term in desc_lower:
                keywords.extend(tech_terms)

        # Deduplicate
        return list(set(keywords))
