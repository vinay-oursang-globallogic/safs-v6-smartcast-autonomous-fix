"""
SAFS v6.0 — Anomaly Detector

Detects log lines whose per-template error rate exceeds a configurable
multiple of the running average (rate-spike detection).

Algorithm
---------
1. Bucket enriched lines into one-minute windows.
2. For each template, compute an average rate across all windows.
3. Flag any window where the template count ≥ baseline_multiplier × average.

Example usage::

    detector = AnomalyDetector(baseline_multiplier=3.0)
    anomalies = detector.detect(enriched_lines)
    for a in anomalies:
        print(a.template_token, "spike:", a.spike_factor)
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from safs.log_analysis.timestamp_extractor import EnrichedLogLine

logger = logging.getLogger(__name__)

_BUCKET_SECONDS = 60  # Window size in seconds


@dataclass
class Anomaly:
    """
    A rate-spike anomaly for a particular log token.

    Attributes:
        template_token: The log token that spiked.
        spike_factor: How many times above the baseline this window reached.
        window_start: Start of the anomalous time window.
        window_end: End of the anomalous time window.
        count_in_window: Number of occurrences in the spike window.
        baseline_avg: Average count per window across all windows.
        severity: ``"critical"`` (>10×), ``"high"`` (>5×), ``"medium"`` (>3×).
    """

    template_token: str
    spike_factor: float
    window_start: Optional[datetime]
    window_end: Optional[datetime]
    count_in_window: int
    baseline_avg: float
    severity: str = "medium"


class AnomalyDetector:
    """
    Rate-spike anomaly detector.

    Args:
        baseline_multiplier: Minimum spike factor (default ``3.0``).
    """

    def __init__(self, baseline_multiplier: float = 3.0) -> None:
        self._multiplier = baseline_multiplier

    def detect(
        self,
        lines: list[EnrichedLogLine],
        baseline_multiplier: Optional[float] = None,
    ) -> list[Anomaly]:
        """
        Detect rate-spike anomalies.

        Args:
            lines: Enriched log lines.
            baseline_multiplier: Override instance threshold.

        Returns:
            List of :class:`Anomaly` sorted by spike_factor descending.
        """
        if baseline_multiplier is not None:
            self._multiplier = baseline_multiplier

        # Bucket lines by minute window
        # {bucket_ts: {token: count}}
        buckets: dict[datetime, dict[str, int]] = defaultdict(
            lambda: defaultdict(int)
        )
        all_tokens: set[str] = set()

        anchor = self._find_anchor(lines)
        if anchor is None:
            return []

        for line in lines:
            ts = line.timestamp or anchor
            bucket = self._bucket_key(ts, anchor)
            tokens = self._extract_tokens(line.raw)
            for tok in tokens:
                buckets[bucket][tok] += 1
                all_tokens.add(tok)

        if not buckets:
            return []

        bucket_list = sorted(buckets.keys())
        anomalies: list[Anomaly] = []

        for token in all_tokens:
            counts = [buckets[b].get(token, 0) for b in bucket_list]
            total = sum(counts)
            if total == 0:
                continue

            n_buckets = len(counts)
            baseline = total / n_buckets

            for i, (bk, count) in enumerate(zip(bucket_list, counts)):
                if baseline == 0 or count == 0:
                    continue
                factor = count / baseline
                if factor >= self._multiplier:
                    bk_end = bk + timedelta(seconds=_BUCKET_SECONDS)
                    if factor >= 10.0:
                        severity = "critical"
                    elif factor >= 5.0:
                        severity = "high"
                    else:
                        severity = "medium"
                    anomalies.append(
                        Anomaly(
                            template_token=token,
                            spike_factor=round(factor, 2),
                            window_start=bk,
                            window_end=bk_end,
                            count_in_window=count,
                            baseline_avg=round(baseline, 2),
                            severity=severity,
                        )
                    )

        anomalies.sort(key=lambda a: a.spike_factor, reverse=True)
        return anomalies

    # ── Private ───────────────────────────────────────────────────────────────

    @staticmethod
    def _find_anchor(lines: list[EnrichedLogLine]) -> Optional[datetime]:
        for line in lines:
            if line.timestamp is not None:
                return line.timestamp
        return None

    @staticmethod
    def _bucket_key(ts: datetime, anchor: datetime) -> datetime:
        delta = (ts - anchor).total_seconds()
        bucket_num = int(delta // _BUCKET_SECONDS)
        return anchor + timedelta(seconds=bucket_num * _BUCKET_SECONDS)

    @staticmethod
    def _extract_tokens(line: str) -> list[str]:
        import re
        tokens = re.findall(r"[A-Z][A-Z0-9_]{3,}", line)
        seen: set[str] = set()
        unique: list[str] = []
        for t in tokens:
            if t not in seen:
                seen.add(t)
                unique.append(t)
        return unique[:3]
