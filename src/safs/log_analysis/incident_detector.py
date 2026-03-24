"""
SAFS v6.0 — Incident Detector

Clusters consecutive error lines into discrete "incidents" separated by
configurable time gaps.  An incident boundary is declared whenever the gap
between adjacent error lines exceeds *gap_seconds*.

Example usage::

    detector = IncidentDetector(gap_seconds=60.0)
    incidents = detector.detect(enriched_lines)
    for inc in incidents:
        print(inc.start_time, "–", inc.end_time, "errors:", inc.error_count)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from safs.log_analysis.timestamp_extractor import EnrichedLogLine

logger = logging.getLogger(__name__)

# ─── Error-level keywords ──────────────────────────────────────────────────────
_ERROR_RE = re.compile(
    r"\b(error|FATAL|SIGSEGV|SIGABRT|crash|fail|oops|panic|abort)\b",
    re.IGNORECASE,
)


@dataclass
class Incident:
    """
    A cluster of temporally close error lines.

    Attributes:
        incident_id: Sequential integer identifier.
        start_time: Timestamp of the first error.
        end_time: Timestamp of the last error.
        error_count: Total number of error lines.
        unique_patterns: Distinct error-keyword set.
        severity: ``"critical"``, ``"high"``, or ``"medium"``.
        lines: Raw log lines in this incident.
    """

    incident_id: int
    start_time: Optional[datetime]
    end_time: Optional[datetime]
    error_count: int = 0
    unique_patterns: set[str] = field(default_factory=set)
    severity: str = "medium"
    lines: list[str] = field(default_factory=list)


class IncidentDetector:
    """
    Cluster consecutive error log lines into incidents.

    Args:
        gap_seconds: Minimum gap (in seconds) between incidents.
    """

    def __init__(self, gap_seconds: float = 60.0) -> None:
        self._gap_seconds = gap_seconds

    def detect(
        self,
        lines: list[EnrichedLogLine],
        gap_seconds: Optional[float] = None,
    ) -> list[Incident]:
        """
        Detect incidents in *lines*.

        Args:
            lines: Enriched log lines.
            gap_seconds: Override instance gap threshold.

        Returns:
            List of :class:`Incident` objects ordered by start time.
        """
        if gap_seconds is not None:
            self._gap_seconds = gap_seconds

        error_lines = [
            line for line in lines if _ERROR_RE.search(line.raw)
        ]

        if not error_lines:
            return []

        incidents: list[Incident] = []
        current_id = 1
        current: list[EnrichedLogLine] = [error_lines[0]]

        for prev, curr in zip(error_lines, error_lines[1:]):
            gap = self._gap_between(prev, curr)
            if gap is None or gap > self._gap_seconds:
                incidents.append(self._build_incident(current_id, current))
                current_id += 1
                current = [curr]
            else:
                current.append(curr)

        incidents.append(self._build_incident(current_id, current))
        return incidents

    # ── Private ───────────────────────────────────────────────────────────────

    @staticmethod
    def _gap_between(
        a: EnrichedLogLine, b: EnrichedLogLine
    ) -> Optional[float]:
        """Return gap in seconds between two lines or ``None`` if unknown."""
        if a.timestamp is None or b.timestamp is None:
            return None
        return (b.timestamp - a.timestamp).total_seconds()

    @staticmethod
    def _build_incident(
        incident_id: int, lines: list[EnrichedLogLine]
    ) -> Incident:
        timestamps = [l.timestamp for l in lines if l.timestamp is not None]
        patterns: set[str] = set()
        for line in lines:
            for m in _ERROR_RE.finditer(line.raw):
                patterns.add(m.group(1).upper())

        # Severity heuristic
        if any(p in patterns for p in ("SIGSEGV", "SIGABRT", "PANIC")):
            severity = "critical"
        elif any(p in patterns for p in ("FATAL", "CRASH", "OOPS")):
            severity = "high"
        else:
            severity = "medium"

        return Incident(
            incident_id=incident_id,
            start_time=min(timestamps) if timestamps else None,
            end_time=max(timestamps) if timestamps else None,
            error_count=len(lines),
            unique_patterns=patterns,
            severity=severity,
            lines=[l.raw for l in lines],
        )
