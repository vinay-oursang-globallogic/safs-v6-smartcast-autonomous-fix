"""
SAFS v6.0 — LOKi Symbolication (ASLR correction + addr2line)

Wraps :class:`~safs.symbol_store.elf_symbolication.ElfSymbolicator` with
ASLR (Address Space Layout Randomization) base-address correction so that
raw crash-log addresses can be resolved to source locations.

Two-step process
----------------
1. **ASLR correction**: subtract the load address of the crashing library
   from each raw address to get the ELF offset address.
2. **addr2line lookup**: feed the offset address to addr2line against the
   matching debug ELF.

Example usage::

    symbolizer = LokiSymbolicator(symbol_store=SymbolStoreClient(...))
    frames = await symbolizer.symbolicate_crash(tombstone_text)
    for frame in frames:
        print(frame.function_name, "@", frame.source_location)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ─── Tombstone parsing regexes ─────────────────────────────────────────────────
_BACKTRACE_RE = re.compile(
    r"#(?P<frame>\d{2})\s+pc\s+(?P<offset>[0-9a-fA-F]+)\s+(?P<lib>/[^\s]+)"
)
_MAPS_RE = re.compile(
    r"(?P<start>[0-9a-fA-F]+)-[0-9a-fA-F]+\s+\S+\s+\S+\s+\S+\s+\S+\s+(?P<path>/[^\s]+)"
)


@dataclass
class LOKiFrame:
    """
    A symbolicated frame from an Android/LOKi tombstone.

    Attributes:
        frame_number: Backtrace frame index (0 = crash site).
        raw_pc: Raw PC address as hex string.
        elf_offset: ASLR-corrected offset as hex string.
        library: Shared library path from the tombstone.
        function_name: Demangled function name or ``"??"``.
        source_location: ``"filename:line"`` or ``"??:0"``.
        symbolicated: True if resolved via addr2line.
    """

    frame_number: int
    raw_pc: str
    elf_offset: str
    library: str
    function_name: str = "??"
    source_location: str = "??:0"
    symbolicated: bool = False


class LokiSymbolicator:
    """
    ASLR-correcting symbolication engine for LOKi crash tombstones.

    Args:
        symbol_store: Storage client used to find debug ELF files.
            If ``None``, symbolication degrades to a best-effort address parse.
    """

    def __init__(self, symbol_store=None) -> None:
        self._store = symbol_store

    async def symbolicate_crash(
        self, tombstone_text: str
    ) -> list[LOKiFrame]:
        """
        Parse a LOKi/Android tombstone and symbolicate each backtrace frame.

        Args:
            tombstone_text: Raw tombstone text (multi-line string).

        Returns:
            List of :class:`LOKiFrame`, frame 0 first (crash site).
        """
        load_bases = self._parse_load_bases(tombstone_text)
        raw_frames = self._parse_backtrace(tombstone_text)

        if not raw_frames:
            return []

        frames: list[LOKiFrame] = []
        for frame_no, raw_pc_hex, library in raw_frames:
            # ASLR correction
            base = load_bases.get(library, 0)
            try:
                raw_pc = int(raw_pc_hex, 16)
                elf_offset = raw_pc - base
                elf_offset_hex = hex(max(0, elf_offset))
            except ValueError:
                elf_offset_hex = "0x0"

            loki_frame = LOKiFrame(
                frame_number=frame_no,
                raw_pc=f"0x{raw_pc_hex}",
                elf_offset=elf_offset_hex,
                library=library,
            )

            # Try to symbolicate if store is available
            if self._store is not None:
                loki_frame = await self._symbolicate_frame(loki_frame)

            frames.append(loki_frame)

        return frames

    # ── Private ───────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_load_bases(text: str) -> dict[str, int]:
        """Extract library load addresses from /proc/pid/maps section."""
        bases: dict[str, int] = {}
        for m in _MAPS_RE.finditer(text):
            lib = m.group("path")
            start = int(m.group("start"), 16)
            if lib not in bases:
                bases[lib] = start
        return bases

    @staticmethod
    def _parse_backtrace(text: str) -> list[tuple]:
        """Extract (frame_no, pc_hex, library) triples from backtrace."""
        frames: list[tuple] = []
        for m in _BACKTRACE_RE.finditer(text):
            frames.append((
                int(m.group("frame")),
                m.group("offset"),
                m.group("lib"),
            ))
        return frames

    async def _symbolicate_frame(self, frame: LOKiFrame) -> LOKiFrame:
        """Attempt to resolve *frame* using the symbol store."""
        try:
            from safs.symbol_store.elf_symbolication import ElfSymbolicator

            # Find debug ELF for this library
            lib_name = Path(frame.library).name
            elf_path = await self._find_debug_elf(lib_name)
            if elf_path is None:
                return frame

            symbolizer = ElfSymbolicator()
            offset = int(frame.elf_offset, 16)
            results = await symbolizer.symbolicate(elf_path, [offset])
            if results:
                r = results[0]
                frame.function_name = r.function_name
                frame.source_location = r.source_location
                from safs.symbol_store.elf_symbolication import SymbolicationStatus
                frame.symbolicated = r.status == SymbolicationStatus.OK
        except Exception as exc:
            logger.debug("Frame symbolication failed: %s", exc)
        return frame

    async def _find_debug_elf(self, lib_name: str) -> Optional[Path]:
        """Look up a debug ELF in the symbol store by library name."""
        if self._store is None:
            return None
        try:
            return await self._store.find_by_library_name(lib_name, "")  # type: ignore[attr-defined]
        except Exception:
            return None
