"""
SAFS v6.0 — JavaScript Source Map Decoder

Looks up source maps for minified Vizio SmartCast streaming-app bundles and
translates ``(line, column)`` positions in the minified output back to their
original TypeScript/JavaScript source locations.

Specification
-------------
Source maps follow the `Source Map Revision 3 Proposal
<https://docs.google.com/document/d/1U1RGAehQwRypUTovF1KRlpiOFze0b-_2gc6fAH0KY0k>`_.
VLQ decoding is implemented in pure Python — no external libraries required.

Example usage::

    store = SourceMapStore(base_path=Path("/opt/safs/symbols/maps"))
    map_path = store.find_map("netflix", "6.0.0")
    if map_path:
        pos = store.decode(map_path, line=1, column=42)
        print(pos.source, pos.original_line, pos.original_column)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ─── VLQ base64 alphabet ──────────────────────────────────────────────────────
_BASE64_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
_BASE64_MAP: dict[str, int] = {c: i for i, c in enumerate(_BASE64_CHARS)}
_VLQ_CONTINUATION_BIT = 1 << 5
_VLQ_BASE_MASK = _VLQ_CONTINUATION_BIT - 1


@dataclass
class SourceMapPosition:
    """
    The original source position corresponding to a generated code position.

    Attributes:
        source: Relative path to the original source file.
        original_line: 1-based line number in the original file.
        original_column: 0-based column in the original line.
        name: Optional symbol name from the ``names`` mapping.
    """

    source: str
    original_line: int
    original_column: int
    name: Optional[str] = None


def _decode_vlq(chars: str) -> list[int]:
    """
    Decode a VLQ base-64 string into a list of signed integers.

    Args:
        chars: A VLQ-encoded string segment (e.g., ``"AAAA"``).

    Returns:
        List of decoded signed integers.
    """
    values: list[int] = []
    shift = 0
    value = 0

    for char in chars:
        digit = _BASE64_MAP.get(char)
        if digit is None:
            break
        continuation = bool(digit & _VLQ_CONTINUATION_BIT)
        digit &= _VLQ_BASE_MASK
        value += digit << shift
        shift += 5

        if not continuation:
            # Convert from sign-magnitude
            if value & 1:
                value = -(value >> 1)
            else:
                value >>= 1
            values.append(value)
            shift = 0
            value = 0

    return values


def _parse_mappings(
    mappings_str: str,
    sources: list[str],
    names: list[str],
) -> list[list[Optional[SourceMapPosition]]]:
    """
    Parse the ``mappings`` field of a source map.

    Returns a list-of-lists indexed by ``[generated_line][generated_column]`` —
    though we only store the first segment per generated column.
    """
    lines: list[list[Optional[SourceMapPosition]]] = []
    src_idx = 0
    orig_line = 0
    orig_col = 0
    name_idx = 0

    for line_str in mappings_str.split(";"):
        segments: list[Optional[SourceMapPosition]] = []
        gen_col = 0
        for seg in line_str.split(","):
            if not seg:
                segments.append(None)
                continue
            vals = _decode_vlq(seg)
            if not vals:
                segments.append(None)
                continue

            gen_col += vals[0]

            if len(vals) < 4:
                segments.append(None)
                continue

            src_idx += vals[1]
            orig_line += vals[2]
            orig_col += vals[3]

            nm: Optional[str] = None
            if len(vals) >= 5:
                name_idx += vals[4]
                nm = names[name_idx] if 0 <= name_idx < len(names) else None

            src_path = (
                sources[src_idx] if 0 <= src_idx < len(sources) else "??"
            )
            segments.append(
                SourceMapPosition(
                    source=src_path,
                    original_line=orig_line + 1,  # 0→1 based
                    original_column=orig_col,
                    name=nm,
                )
            )
        lines.append(segments)

    return lines


class SourceMapStore:
    """
    Filesystem store for JavaScript source maps.

    Source maps are stored under ``base_path/{app}/{version}/bundle.js.map``.

    Args:
        base_path: Root directory containing source map archives.
        lru_maxsize: How many decoded source maps to keep in memory.
    """

    def __init__(
        self,
        base_path: Optional[Path] = None,
        lru_maxsize: int = 50,
    ) -> None:
        self._base = base_path or Path("/opt/safs/symbols/maps")
        self._lru_maxsize = lru_maxsize
        # Maps are decoded lazily and cached by path string
        self._cache: dict[str, list[list[Optional[SourceMapPosition]]]] = {}
        self._cache_order: list[str] = []

    def find_map(self, app: str, version: str) -> Optional[Path]:
        """
        Locate the source map for *app* at *version*.

        Searches ``{base_path}/{app}/{version}/*.js.map`` and returns the
        first match.

        Args:
            app: Application name (e.g., ``"netflix"``, ``"hulu"``).
            version: Bundle version string.

        Returns:
            Path to the ``.js.map`` file or ``None`` if not found.
        """
        candidates = [
            self._base / app / version / "bundle.js.map",
            self._base / app / version / "main.js.map",
            self._base / app / f"{version}.js.map",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate

        # Glob fallback
        app_dir = self._base / app / version
        if app_dir.is_dir():
            maps = list(app_dir.glob("*.js.map"))
            if maps:
                return maps[0]

        return None

    def decode(
        self,
        map_path: Path,
        line: int,
        column: int,
    ) -> Optional[SourceMapPosition]:
        """
        Translate a generated (line, column) position back to source.

        Args:
            map_path: Path to a ``.js.map`` file.
            line: 1-based generated line number.
            column: 0-based generated column number.

        Returns:
            :class:`SourceMapPosition` or ``None`` if mapping not found.

        Raises:
            FileNotFoundError: If *map_path* does not exist.
            ValueError: If the source map JSON is malformed.
        """
        if not map_path.exists():
            raise FileNotFoundError(f"Source map not found: {map_path}")

        parsed = self._load(map_path)
        line_idx = line - 1  # convert to 0-based

        if line_idx < 0 or line_idx >= len(parsed):
            return None

        segments = parsed[line_idx]
        # Return the last segment with column ≤ requested column
        result: Optional[SourceMapPosition] = None
        for seg in segments:
            if seg is None:
                continue
            result = seg
            if seg.original_column > column:
                break

        return result

    # ── Private ───────────────────────────────────────────────────────────────

    def _load(
        self, map_path: Path
    ) -> list[list[Optional[SourceMapPosition]]]:
        """Load and decode a source map, using the LRU cache."""
        key = str(map_path)
        if key in self._cache:
            return self._cache[key]

        raw = map_path.read_text(encoding="utf-8")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid source map JSON in {map_path}: {exc}") from exc

        sources: list[str] = data.get("sources", [])
        names: list[str] = data.get("names", [])
        mappings: str = data.get("mappings", "")

        parsed = _parse_mappings(mappings, sources, names)

        # Evict LRU if at capacity
        if len(self._cache) >= self._lru_maxsize and self._cache_order:
            oldest = self._cache_order.pop(0)
            self._cache.pop(oldest, None)

        self._cache[key] = parsed
        self._cache_order.append(key)
        return parsed
