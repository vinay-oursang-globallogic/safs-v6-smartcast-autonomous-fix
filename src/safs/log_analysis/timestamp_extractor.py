"""
SAFS v6.0 — Multi-format Timestamp Extractor

Parses timestamps from six distinct log formats produced by the Vizio SmartCast
platform, attaching parsed ``datetime`` objects to raw log lines.

Supported formats
-----------------
1. Kernel dmesg       ``[   123.456789]``
2. DTV service        ``2024/03/15 14:30:25.123``
3. SCPL / SmartCast   ``2024-03-15 14:30:25.123``
4. Syslog RFC 3164    ``Mar 15 14:30:25``
5. ISO 8601           ``2024-03-15T14:30:25.123Z``
6. Android Logcat     ``03-15 14:30:25.123``
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

# ─── Timestamp patterns ───────────────────────────────────────────────────────
_KERNEL_UPTIME_RE = re.compile(r"^\[(\s*\d+\.\d+)\]")
_DTV_SVC_RE = re.compile(r"(\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2}\.\d+)")
_SCPL_RE = re.compile(r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?)")
_SYSLOG_RE = re.compile(
    r"([A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})"
)
_ISO_8601_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?)"
)
_LOGCAT_RE = re.compile(r"(\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d+)")

_SYSLOG_MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


@dataclass
class EnrichedLogLine:
    """A log line augmented with its parsed timestamp.

    Attributes:
        raw: Original unparsed line text.
        timestamp: Parsed ``datetime`` in UTC, or ``None`` if unparseable.
        line_number: 1-based position in the source file.
        format_name: Name of the matched timestamp format.
    """

    raw: str
    timestamp: Optional[datetime] = None
    line_number: int = 0
    format_name: str = "unknown"


class TimestampExtractor:
    """
    Multi-format timestamp extractor for Vizio SmartCast log files.

    Example usage::

        extractor = TimestampExtractor()
        ts = extractor.extract("[  12.345678] LOKi crashed")
        enriched = extractor.enrich_lines(lines)
    """

    def extract(self, line: str) -> Optional[datetime]:
        """
        Parse the first timestamp found in *line*.

        Tries each format in priority order and returns on the first match.

        Args:
            line: A single log line (any format).

        Returns:
            UTC-aware ``datetime`` if a timestamp was found, else ``None``.
        """
        # 1. ISO 8601 — most precise, check first
        m = _ISO_8601_RE.search(line)
        if m:
            return self._parse_iso8601(m.group(1))

        # 2. Kernel uptime — second most common
        m = _KERNEL_UPTIME_RE.search(line)
        if m:
            return self._parse_kernel_uptime(m.group(1))

        # 3. SCPL / SmartCast timestamp
        m = _SCPL_RE.search(line)
        if m:
            return self._parse_scpl(m.group(1))

        # 4. DTV service
        m = _DTV_SVC_RE.search(line)
        if m:
            return self._parse_dtv(m.group(1))

        # 5. Android logcat
        m = _LOGCAT_RE.search(line)
        if m:
            return self._parse_logcat(m.group(1))

        # 6. Syslog RFC 3164
        m = _SYSLOG_RE.search(line)
        if m:
            return self._parse_syslog(m.group(1))

        return None

    def enrich_lines(
        self, lines: list[str], year: int = 2024
    ) -> list[EnrichedLogLine]:
        """
        Attach parsed timestamps to each line in *lines*.

        Args:
            lines: Raw log lines.
            year: Calendar year to use when the format lacks a year component
                  (syslog, logcat).

        Returns:
            List of :class:`EnrichedLogLine` objects; timestamps are ``None``
            for lines that did not match any format.
        """
        self._ref_year = year
        enriched: list[EnrichedLogLine] = []
        for i, raw in enumerate(lines, start=1):
            ts = self.extract(raw)
            fmt = self._last_format
            enriched.append(
                EnrichedLogLine(
                    raw=raw,
                    timestamp=ts,
                    line_number=i,
                    format_name=fmt,
                )
            )
        return enriched

    # ── Private helpers ───────────────────────────────────────────────────────

    def __init__(self) -> None:
        self._ref_year: int = 2024
        self._last_format: str = "unknown"

    def _parse_iso8601(self, s: str) -> Optional[datetime]:
        self._last_format = "iso8601"
        s = s.rstrip("Z")
        for fmt in (
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
        ):
            try:
                dt = datetime.strptime(s, fmt)
                return dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return None

    def _parse_kernel_uptime(self, s: str) -> Optional[datetime]:
        self._last_format = "kernel_uptime"
        # Return epoch + uptime as a sortable proxy timestamp
        try:
            seconds = float(s.strip())
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except ValueError:
            return None

    def _parse_scpl(self, s: str) -> Optional[datetime]:
        self._last_format = "scpl"
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(s, fmt).replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                continue
        return None

    def _parse_dtv(self, s: str) -> Optional[datetime]:
        self._last_format = "dtv_svc"
        # Normalise slash-separated date
        s = s.replace("/", "-").replace("  ", " ")
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(s, fmt).replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                continue
        return None

    def _parse_logcat(self, s: str) -> Optional[datetime]:
        self._last_format = "logcat"
        try:
            # Prepend ref year to avoid Python 3.14+ ambiguous-date DeprecationWarning
            dt = datetime.strptime(f"{self._ref_year}-{s}", "%Y-%m-%d %H:%M:%S.%f")
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    def _parse_syslog(self, s: str) -> Optional[datetime]:
        self._last_format = "syslog"
        parts = s.split()
        if len(parts) < 3:
            return None
        month = _SYSLOG_MONTHS.get(parts[0])
        if month is None:
            return None
        try:
            day = int(parts[1])
            h, m, sec = parts[2].split(":")
            return datetime(
                self._ref_year,
                month,
                day,
                int(h),
                int(m),
                int(sec),
                tzinfo=timezone.utc,
            )
        except (ValueError, IndexError):
            return None
