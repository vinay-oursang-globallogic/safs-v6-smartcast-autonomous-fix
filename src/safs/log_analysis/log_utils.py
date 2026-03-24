"""
SAFS v6.0 — Log Utilities

Shared utilities for log file parsing, normalization, and streaming.

Provides:
- ANSI escape code stripping
- Log level extraction
- Binary content detection
- Streaming chunk reader for GB-scale log files
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator, Optional


# ─── ANSI escape sequences ───────────────────────────────────────────────────
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[mABCDEFGHJKSTfhilmnpsu]")

# ─── Log level detection ──────────────────────────────────────────────────────
_LOG_LEVEL_RE = re.compile(
    r"\b(VERBOSE|DEBUG|INFO|WARNING|WARN|ERROR|ERR|CRITICAL|FATAL|WTF)\b",
    re.IGNORECASE,
)

# Level normalisation map
_LEVEL_MAP: dict[str, str] = {
    "verbose": "VERBOSE",
    "debug": "DEBUG",
    "info": "INFO",
    "warning": "WARNING",
    "warn": "WARNING",
    "error": "ERROR",
    "err": "ERROR",
    "critical": "CRITICAL",
    "fatal": "FATAL",
    "wtf": "FATAL",  # Android "What a Terrible Failure"
}

# ─── Binary content heuristics ───────────────────────────────────────────────
_BINARY_THRESHOLD = 0.30  # fraction of non-printable bytes that flags binary
_TEXT_PRINTABLE_RE = re.compile(rb"[\x09\x0a\x0d\x20-\x7e]")


def normalize_log_line(line: str) -> str:
    """
    Strip ANSI escape sequences and normalize whitespace in a single log line.

    Args:
        line: Raw log line, possibly containing colour codes.

    Returns:
        Cleaned line with ANSI codes removed and internal whitespace collapsed.
    """
    clean = _ANSI_RE.sub("", line)
    # Collapse multiple spaces/tabs into a single space, strip trailing newline
    clean = " ".join(clean.split())
    return clean


def extract_log_level(line: str) -> Optional[str]:
    """
    Return the first log-level token found in *line*.

    Args:
        line: A single log line.

    Returns:
        Normalised level string (e.g., ``"WARNING"``) or ``None`` if not found.
    """
    match = _LOG_LEVEL_RE.search(line)
    if match:
        return _LEVEL_MAP.get(match.group(1).lower(), match.group(1).upper())
    return None


def is_binary_content(data: bytes) -> bool:
    """
    Heuristically determine whether *data* represents binary (non-text) content.

    Uses the fraction of printable ASCII bytes as the signal; anything below
    ``1 - _BINARY_THRESHOLD`` (70%) is classified as binary.

    Args:
        data: Raw bytes to test (typically the first 8 KiB of a file).

    Returns:
        ``True`` if the data appears to be binary.
    """
    if not data:
        return False
    printable = len(_TEXT_PRINTABLE_RE.findall(data))
    ratio = printable / len(data)
    return ratio < (1 - _BINARY_THRESHOLD)


def chunk_log_file(
    path: Path,
    chunk_size: int = 10_000,
    encoding: str = "utf-8",
    errors: str = "replace",
) -> Iterator[list[str]]:
    """
    Stream a (potentially multi-GB) log file in fixed-size chunks of lines.

    Memory usage is bounded to approximately ``chunk_size`` lines regardless of
    file size.

    Args:
        path: Absolute path to the log file.
        chunk_size: Number of lines per yielded chunk.
        encoding: File encoding.
        errors: Encoding error handler (``"replace"`` keeps non-UTF-8 bytes).

    Yields:
        Lists of up to ``chunk_size`` stripped log lines.

    Raises:
        FileNotFoundError: If *path* does not exist.
        OSError: On any other IO failure.
    """
    buffer: list[str] = []
    with path.open(encoding=encoding, errors=errors) as fh:
        for raw_line in fh:
            buffer.append(raw_line.rstrip("\n"))
            if len(buffer) >= chunk_size:
                yield buffer
                buffer = []
    if buffer:
        yield buffer
