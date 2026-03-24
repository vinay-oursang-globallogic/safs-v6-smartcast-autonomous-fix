"""
SAFS v6.0 — Cascading Failure Detector

Identifies temporal cause→effect chains by finding groups of error tokens that
consistently follow each other in a fixed ordering within a time window.

Algorithm
---------
1. Extract timestamped error events from enriched lines.
2. For each pair (A, B) where A consistently precedes B in
   :attr:`~ErrorCorrelation` pairs, look for a third event C that follows B.
3. Score chains by the fraction of windows where the full A→B→C ordering holds.

Example usage::

    detector = CascadingFailureDetector()
    chains = detector.detect(enriched_lines, correlations)
    for chain in chains:
        print(" → ".join(chain.chain))
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Optional

from safs.log_analysis.timestamp_extractor import EnrichedLogLine
from safs.log_analysis.correlation_engine import ErrorCorrelation

logger = logging.getLogger(__name__)

_CHAIN_WINDOW_SECONDS = 30.0  # Max time span for a single chain occurrence


@dataclass
class CascadingFailure:
    """
    A temporal cause-effect chain of error tokens.

    Attributes:
        chain: Ordered list of error tokens (at least two elements).
        confidence: Fraction of occurrences where full order holds [0, 1].
        occurrence_count: Number of times the chain was observed.
    """

    chain: list[str]
    confidence: float
    occurrence_count: int


class CascadingFailureDetector:
    """
    Detect A → B → C temporal error chains.

    Uses the output of :class:`CorrelationEngine` as a seed for candidate pairs,
    then searches for consistent ordering.

    Args:
        window_seconds: Maximum time span of a single chain.
        min_occurrences: Minimum times a chain must appear to be reported.
    """

    def __init__(
        self,
        window_seconds: float = _CHAIN_WINDOW_SECONDS,
        min_occurrences: int = 2,
    ) -> None:
        self._window = timedelta(seconds=window_seconds)
        self._min_occurrences = min_occurrences

    def detect(
        self,
        lines: list[EnrichedLogLine],
        correlations: list[ErrorCorrelation],
    ) -> list[CascadingFailure]:
        """
        Find cascading failure chains.

        Args:
            lines: Enriched log lines.
            correlations: Pre-computed correlations (from
                :class:`~safs.log_analysis.correlation_engine.CorrelationEngine`).

        Returns:
            List of :class:`CascadingFailure` sorted by confidence descending.
        """
        import re

        # Build event sequence: [(timestamp, token), ...]
        events: list[tuple] = []
        for line in lines:
            if line.timestamp is None:
                continue
            tokens = re.findall(r"[A-Z][A-Z0-9_]{3,}", line.raw)
            for tok in set(tokens):
                events.append((line.timestamp, tok))

        events.sort(key=lambda e: e[0])

        if len(events) < 3:
            return []

        # Seed with correlated pairs → build ordered pair graph
        ordered_pairs: dict[tuple[str, str], int] = defaultdict(int)
        for corr in correlations:
            # Determine consistent ordering in event stream
            a_times = [ts for ts, tok in events if tok == corr.pattern_a]
            b_times = [ts for ts, tok in events if tok == corr.pattern_b]
            if a_times and b_times:
                # Count windows where min(A) < min(B) within _window
                first_a = min(a_times)
                subsequent_b = [t for t in b_times if first_a <= t <= first_a + self._window]
                if subsequent_b:
                    ordered_pairs[(corr.pattern_a, corr.pattern_b)] += len(subsequent_b)

        # Extend pairs to triples
        chains_map: dict[tuple, list[int]] = defaultdict(list)
        pair_list = list(ordered_pairs.keys())

        for ab in pair_list:
            a, b = ab
            for bc in pair_list:
                if bc[0] == b and bc[1] != a:
                    triple = (a, b, bc[1])
                    # Count occurrences
                    count = self._count_chain(events, triple)
                    if count >= self._min_occurrences:
                        chains_map[triple].append(count)

        # Also add pairs directly
        for (a, b), count in ordered_pairs.items():
            if count >= self._min_occurrences:
                chains_map[(a, b)].append(count)

        # Build output
        results: list[CascadingFailure] = []
        total_events = len(events)
        for chain_tokens, counts in chains_map.items():
            occ = max(counts)
            confidence = min(1.0, occ / max(total_events / 10.0, 1.0))
            results.append(
                CascadingFailure(
                    chain=list(chain_tokens),
                    confidence=round(confidence, 3),
                    occurrence_count=occ,
                )
            )

        results.sort(key=lambda c: c.confidence, reverse=True)
        return results[:50]

    # ── Private ───────────────────────────────────────────────────────────────

    def _count_chain(
        self, events: list[tuple], chain: tuple[str, ...]
    ) -> int:
        """Count how many times *chain* appears in order within window."""
        count = 0
        first_tok = chain[0]
        first_times = [ts for ts, tok in events if tok == first_tok]

        for start_ts in first_times:
            window_end = start_ts + self._window
            window_events = [(ts, tok) for ts, tok in events if start_ts <= ts <= window_end]
            # Check all tokens appear in order
            if self._appears_in_order(window_events, chain):
                count += 1

        return count

    @staticmethod
    def _appears_in_order(
        window_events: list[tuple], chain: tuple[str, ...]
    ) -> bool:
        """Return True if all tokens in *chain* appear in order."""
        idx = 0
        for _, tok in window_events:
            if tok == chain[idx]:
                idx += 1
                if idx == len(chain):
                    return True
        return False
