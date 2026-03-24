"""
SAFS v6.0 — Temporal Error Correlation Engine

Identifies pairs of error patterns that co-occur within a configurable time
window, scoring them by frequency and temporal proximity.

Example usage::

    engine = CorrelationEngine(window_seconds=5.0)
    correlations = engine.analyze(enriched_lines)
    for c in correlations:
        print(c.pattern_a, "→", c.pattern_b, "score:", c.score)
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from itertools import combinations
from typing import Optional

from safs.log_analysis.timestamp_extractor import EnrichedLogLine

logger = logging.getLogger(__name__)

# ─── Constants ─────────────────────────────────────────────────────────────────
_MAX_CORRELATIONS = 200  # Cap output list size


@dataclass
class ErrorCorrelation:
    """
    A pair of error-pattern tokens that co-occur within a time window.

    Attributes:
        pattern_a: First pattern token (template_str or error keyword).
        pattern_b: Second pattern token.
        co_occurrence_count: Number of time windows containing both.
        avg_delta_seconds: Average time gap between the two in each window.
        score: Composite correlation score in [0.0, 1.0].
    """

    pattern_a: str
    pattern_b: str
    co_occurrence_count: int = 0
    avg_delta_seconds: float = 0.0
    score: float = 0.0


class CorrelationEngine:
    """
    Temporal error correlation engine.

    Groups enriched log lines into sliding windows and counts how often each
    error-pattern pair appears together.  Lines without timestamps are included
    in the window of the nearest preceding timestamped line.

    Args:
        window_seconds: Half-width of the sliding time window in seconds.
    """

    def __init__(self, window_seconds: float = 5.0) -> None:
        self._window = timedelta(seconds=window_seconds)

    def analyze(
        self,
        lines: list[EnrichedLogLine],
        window_seconds: Optional[float] = None,
    ) -> list[ErrorCorrelation]:
        """
        Discover correlated error pairs within the given time window.

        Args:
            lines: Enriched log lines (from :class:`TimestampExtractor`).
            window_seconds: Override the instance window if provided.

        Returns:
            Up to :pydata:`_MAX_CORRELATIONS` :class:`ErrorCorrelation` objects
            sorted by score descending.
        """
        if window_seconds is not None:
            self._window = timedelta(seconds=window_seconds)

        # Collect (timestamp, token) pairs; skip lines without timestamps
        events: list[tuple[datetime, str]] = []
        for line in lines:
            if line.timestamp is None:
                continue
            tokens = self._extract_tokens(line.raw)
            for tok in tokens:
                events.append((line.timestamp, tok))

        if len(events) < 2:
            return []

        # Sort by time
        events.sort(key=lambda e: e[0])

        # Sliding window: for each event find all events within +/- window
        pair_counts: dict[tuple[str, str], list[float]] = defaultdict(list)

        for i, (ts_a, tok_a) in enumerate(events):
            window_end = ts_a + self._window
            for j in range(i + 1, len(events)):
                ts_b, tok_b = events[j]
                if ts_b > window_end:
                    break
                if tok_a == tok_b:
                    continue
                key = tuple(sorted([tok_a, tok_b]))  # type: ignore[assignment]
                delta = (ts_b - ts_a).total_seconds()
                pair_counts[key].append(delta)  # type: ignore[arg-type]

        correlations: list[ErrorCorrelation] = []
        for (a, b), deltas in pair_counts.items():
            count = len(deltas)
            avg_delta = sum(deltas) / count
            # Score: normalised count × proximity (lower delta = higher score)
            proximity = 1.0 / (1.0 + avg_delta)
            score = min(1.0, (count / 10.0) * proximity)
            correlations.append(
                ErrorCorrelation(
                    pattern_a=a,
                    pattern_b=b,
                    co_occurrence_count=count,
                    avg_delta_seconds=avg_delta,
                    score=score,
                )
            )

        correlations.sort(key=lambda c: c.score, reverse=True)
        return correlations[:_MAX_CORRELATIONS]

    # ── Private ───────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_tokens(line: str) -> list[str]:
        """Extract meaningful tokens from a log line."""
        import re

        # Keep only tokens that look like identifiers / error names
        tokens = re.findall(r"[A-Z][A-Z0-9_]{3,}|[A-Za-z][a-zA-Z0-9_]{4,}", line)
        # Deduplicate while preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for t in tokens:
            if t not in seen:
                seen.add(t)
                unique.append(t)
        return unique[:5]  # Cap per-line contribution
