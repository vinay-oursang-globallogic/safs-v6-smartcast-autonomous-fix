"""
SAFS v6.0 — Token-Aware Context Compressor

Compresses LLM context windows while protecting critical diagnostic signals
(system prompts, error categories, crash backtraces) from truncation.

Two modes
---------
1. **LLMLingua-2** (optional): Uses Microsoft's LLMLingua-2 model for
   high-fidelity compression when the package is installed.
2. **Heuristic** (always available): Removes blank lines, collapses repetitive
   patterns (redundant log lines), and truncates long code blocks.

Activation threshold
--------------------
The compressor is only invoked when context exceeds ``activation_tokens``
(default 150,000 tokens), estimated via ``len(text) // 4``.

Example usage::

    compressor = SyntaxAwareCompressor(target_ratio=0.5)
    compressed = compressor.compress(raw_context)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# ─── Constants ─────────────────────────────────────────────────────────────────
_ACTIVATION_TOKENS = 150_000      # Activate compression above this size
_CHARS_PER_TOKEN = 4              # Rough estimate
_MAX_CODE_BLOCK_LINES = 50        # Truncate code blocks beyond this many lines

# Protected patterns — never compressed
_PROTECTED_PATTERNS: list[re.Pattern] = [
    re.compile(r"## SYSTEM PROMPT", re.IGNORECASE),
    re.compile(r"ErrorCategory\.|BugLayer\.", re.IGNORECASE),
    re.compile(r"SIGSEGV|SIGABRT|stack trace:|backtrace:", re.IGNORECASE),
    re.compile(r"ConfidenceResult|FixCandidate|PipelineState", re.IGNORECASE),
    re.compile(r"#00 pc|Fatal signal|Abort message", re.IGNORECASE),
]


def _estimate_tokens(text: str) -> int:
    """Estimate token count from character length."""
    return len(text) // _CHARS_PER_TOKEN


def _is_protected(line: str) -> bool:
    """Return True if *line* should never be removed."""
    return any(p.search(line) for p in _PROTECTED_PATTERNS)


@dataclass
class CompressionResult:
    """
    Output of :meth:`SyntaxAwareCompressor.compress`.

    Attributes:
        compressed_text: The compressed context string.
        original_tokens: Estimated token count before compression.
        compressed_tokens: Estimated token count after compression.
        ratio_achieved: Actual compression ratio (compressed / original).
        method_used: ``"llmlingua2"`` or ``"heuristic"``.
    """

    compressed_text: str
    original_tokens: int
    compressed_tokens: int
    ratio_achieved: float
    method_used: str


class SyntaxAwareCompressor:
    """
    Compress LLM context windows while protecting diagnostic signals.

    Args:
        target_ratio: Desired compression ratio (``0.5`` = halve the text).
        activation_tokens: Minimum token count to trigger compression.
    """

    def __init__(
        self,
        target_ratio: float = 0.5,
        activation_tokens: int = _ACTIVATION_TOKENS,
    ) -> None:
        if not 0.1 <= target_ratio < 1.0:
            raise ValueError(f"target_ratio must be in [0.1, 1.0), got {target_ratio}")
        self._target_ratio = target_ratio
        self._activation_tokens = activation_tokens
        self._llmlingua_available = self._detect_llmlingua()

    def compress(
        self,
        context: str,
        target_ratio: Optional[float] = None,
    ) -> str:
        """
        Compress *context* to approximately *target_ratio* of its original size.

        Only activates when context exceeds the activation threshold.

        Args:
            context: Raw LLM context string.
            target_ratio: Override the instance target ratio.

        Returns:
            Compressed string (or original if below threshold).
        """
        ratio = target_ratio if target_ratio is not None else self._target_ratio
        original_tokens = _estimate_tokens(context)

        if original_tokens < self._activation_tokens:
            logger.debug(
                "Context %d tokens < threshold %d; skipping compression",
                original_tokens,
                self._activation_tokens,
            )
            return context

        logger.info(
            "Compressing context: %d tokens (target ratio=%.1f)",
            original_tokens,
            ratio,
        )

        if self._llmlingua_available:
            result = self._compress_llmlingua(context, ratio)
        else:
            result = self._compress_heuristic(context, ratio)

        compressed_tokens = _estimate_tokens(result.compressed_text)
        logger.info(
            "Compression complete: %d → %d tokens (%.0f%%) via %s",
            original_tokens,
            compressed_tokens,
            (1 - result.ratio_achieved) * 100,
            result.method_used,
        )

        return result.compressed_text

    def compress_detailed(
        self, context: str, target_ratio: Optional[float] = None
    ) -> CompressionResult:
        """
        Compress *context* and return detailed metrics.

        Args:
            context: Raw LLM context string.
            target_ratio: Override instance ratio.

        Returns:
            :class:`CompressionResult` with statistics.
        """
        ratio = target_ratio if target_ratio is not None else self._target_ratio
        original_tokens = _estimate_tokens(context)

        if original_tokens < self._activation_tokens:
            return CompressionResult(
                compressed_text=context,
                original_tokens=original_tokens,
                compressed_tokens=original_tokens,
                ratio_achieved=1.0,
                method_used="none",
            )

        if self._llmlingua_available:
            result = self._compress_llmlingua(context, ratio)
        else:
            result = self._compress_heuristic(context, ratio)

        result.original_tokens = original_tokens
        result.compressed_tokens = _estimate_tokens(result.compressed_text)
        result.ratio_achieved = (
            result.compressed_tokens / original_tokens if original_tokens > 0 else 1.0
        )
        return result

    # ── Private ───────────────────────────────────────────────────────────────

    @staticmethod
    def _detect_llmlingua() -> bool:
        """Check whether llmlingua is importable."""
        try:
            import llmlingua  # noqa: F401
            return True
        except ImportError:
            return False

    def _compress_llmlingua(
        self, context: str, ratio: float
    ) -> CompressionResult:
        """Compress using LLMLingua-2 with protected blocks."""
        try:
            from llmlingua import PromptCompressor  # type: ignore[import]

            compressor = PromptCompressor(
                model_name="microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank",
                use_llmlingua2=True,
            )
            result = compressor.compress_prompt(
                context,
                rate=ratio,
                force_tokens=["##", "```", "SYSTEM"],
            )
            compressed = result.get("compressed_prompt", context)
            return CompressionResult(
                compressed_text=compressed,
                original_tokens=_estimate_tokens(context),
                compressed_tokens=_estimate_tokens(compressed),
                ratio_achieved=_estimate_tokens(compressed) / max(_estimate_tokens(context), 1),
                method_used="llmlingua2",
            )
        except Exception as exc:
            logger.warning("LLMLingua-2 failed: %s; falling back to heuristic", exc)
            return self._compress_heuristic(context, ratio)

    def _compress_heuristic(
        self, context: str, ratio: float
    ) -> CompressionResult:
        """
        Token-budget-aware heuristic compression.

        Steps applied in order until target ratio is met:
        1. Remove blank lines.
        2. Collapse repeated consecutive identical lines.
        3. Truncate long code blocks (> _MAX_CODE_BLOCK_LINES lines).
        4. Drop low-signal lines (timestamps, debug-level entries).
        """
        lines = context.splitlines()
        original_count = len(lines)

        # Step 1: Remove blank lines
        lines = [l for l in lines if l.strip() or _is_protected(l)]

        # Step 2: Collapse repeated consecutive lines
        deduped: list[str] = []
        prev: Optional[str] = None
        repeat_count = 0
        for line in lines:
            if line == prev and not _is_protected(line):
                repeat_count += 1
                if repeat_count == 1:
                    deduped.append(f"  [... repeated ...]")
            else:
                deduped.append(line)
                prev = line
                repeat_count = 0
        lines = deduped

        # Step 3: Truncate code blocks
        lines = self._truncate_code_blocks(lines)

        # Step 4: Drop low-signal lines if still over budget
        target_lines = int(len(lines) * ratio)
        if len(lines) > target_lines:
            lines = self._drop_low_signal(lines, target_lines)

        compressed = "\n".join(lines)
        return CompressionResult(
            compressed_text=compressed,
            original_tokens=0,
            compressed_tokens=0,
            ratio_achieved=len(lines) / max(original_count, 1),
            method_used="heuristic",
        )

    @staticmethod
    def _truncate_code_blocks(lines: list[str]) -> list[str]:
        """Truncate code blocks that exceed _MAX_CODE_BLOCK_LINES lines."""
        result: list[str] = []
        in_code = False
        code_line_count = 0

        for line in lines:
            if line.strip().startswith("```"):
                in_code = not in_code
                code_line_count = 0
                result.append(line)
                continue

            if in_code:
                code_line_count += 1
                if code_line_count <= _MAX_CODE_BLOCK_LINES:
                    result.append(line)
                elif code_line_count == _MAX_CODE_BLOCK_LINES + 1:
                    result.append("  // ... [truncated] ...")
            else:
                result.append(line)

        return result

    @staticmethod
    def _drop_low_signal(lines: list[str], target: int) -> list[str]:
        """Remove low-signal lines until *target* count is reached."""
        _LOW_SIGNAL_RE = re.compile(
            r"^\s*(?:\d{4}-\d{2}-\d{2}|\[\s*\d+\.\d+\])\s+\[DEBUG\]",
            re.IGNORECASE,
        )
        protected = [l for l in lines if _is_protected(l)]
        droppable = [l for l in lines if not _is_protected(l) and not _LOW_SIGNAL_RE.match(l)]
        low_signal = [l for l in lines if not _is_protected(l) and _LOW_SIGNAL_RE.match(l)]

        # Build result: keep all protected + as many droppable as budget allows
        budget = max(0, target - len(protected))
        result = protected + droppable[:budget]
        return result
