"""
SAFS v6.0 — ELF Symbolication (addr2line wrapper)

Converts raw memory addresses (from LOKi tombstones / crash reports) into
human-readable ``filename:line_number (function_name)`` symbol strings using
the ``addr2line`` command-line tool from the ARM cross-compilation toolchain.

Features
--------
- Async subprocess execution via :mod:`asyncio`
- Batch multiple addresses in a single ``addr2line`` call
- Handles missing debug info gracefully (``SymbolicationStatus.NO_DEBUG_INFO``)
- Supports arm-linux-gnueabi-addr2line if available

Example usage::

    symbolizer = ElfSymbolicator()
    frames = await symbolizer.symbolicate(
        elf_path=Path("loki_core.debug"),
        addresses=[0xABCD1234, 0xDEADBEEF],
    )
    for frame in frames:
        print(frame.address_hex, frame.function_name, frame.source_location)
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ─── Tool discovery ────────────────────────────────────────────────────────────
_ADDR2LINE_CANDIDATES: list[str] = [
    "arm-linux-gnueabi-addr2line",
    "arm-linux-gnueabihf-addr2line",
    "addr2line",
]

_ADDR2LINE_TIMEOUT = 30  # seconds per batch


class SymbolicationStatus(str, Enum):
    """Outcome of a single address symbolication attempt."""

    OK = "ok"
    NO_DEBUG_INFO = "no_debug_info"
    TOOL_NOT_FOUND = "tool_not_found"
    PARSE_ERROR = "parse_error"
    TIMEOUT = "timeout"


@dataclass
class SymbolicatedFrame:
    """
    Result of symbolising a single address.

    Attributes:
        address: Original integer address.
        address_hex: Address as ``0x``-prefixed hex string.
        function_name: Demangled function name or ``"??"``.
        source_location: ``"filename:line_number"`` or ``"??:0"``.
        status: Symbolication outcome.
    """

    address: int
    address_hex: str
    function_name: str
    source_location: str
    status: SymbolicationStatus


def _find_addr2line() -> Optional[str]:
    """Locate an addr2line binary in ``PATH``."""
    for candidate in _ADDR2LINE_CANDIDATES:
        path = shutil.which(candidate)
        if path:
            return path
    return None


class ElfSymbolicator:
    """
    Async addr2line wrapper for ARM ELF debug symbols.

    Args:
        addr2line_path: Explicit path to addr2line binary; auto-detected
            from PATH if ``None``.
    """

    def __init__(
        self, addr2line_path: Optional[str] = None
    ) -> None:
        self._binary = addr2line_path or _find_addr2line()
        if self._binary:
            logger.debug("ElfSymbolicator using binary: %s", self._binary)
        else:
            logger.warning(
                "No addr2line binary found; symbolication will return "
                "NO_DEBUG_INFO status"
            )

    async def symbolicate(
        self,
        elf_path: Path,
        addresses: list[int],
    ) -> list[SymbolicatedFrame]:
        """
        Symbolicate a list of memory addresses against an ELF debug file.

        Args:
            elf_path: Path to the ELF or ``.debug`` file with DWARF info.
            addresses: List of integer addresses to resolve.

        Returns:
            One :class:`SymbolicatedFrame` per address, in input order.

        Raises:
            FileNotFoundError: If *elf_path* does not exist.
        """
        if not elf_path.exists():
            raise FileNotFoundError(f"ELF debug file not found: {elf_path}")

        if not addresses:
            return []

        if self._binary is None:
            return [
                self._make_frame(addr, SymbolicationStatus.TOOL_NOT_FOUND)
                for addr in addresses
            ]

        hex_addrs = [hex(a) for a in addresses]
        try:
            output = await self._run_addr2line(elf_path, hex_addrs)
            return self._parse_output(addresses, output)
        except asyncio.TimeoutError:
            logger.error("addr2line timed out for %s", elf_path)
            return [
                self._make_frame(a, SymbolicationStatus.TIMEOUT)
                for a in addresses
            ]
        except Exception as exc:
            logger.error("addr2line error: %s", exc)
            return [
                self._make_frame(a, SymbolicationStatus.PARSE_ERROR)
                for a in addresses
            ]

    # ── Private ───────────────────────────────────────────────────────────────

    async def _run_addr2line(
        self, elf_path: Path, hex_addrs: list[str]
    ) -> str:
        """Invoke addr2line and return its stdout."""
        cmd = [
            self._binary,       # type: ignore[list-item]
            "-f",               # include function names
            "-C",               # demangle C++ names
            "-e", str(elf_path),
        ] + hex_addrs

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _ = await asyncio.wait_for(
                proc.communicate(), timeout=_ADDR2LINE_TIMEOUT
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            raise

        return stdout.decode("utf-8", errors="replace")

    def _parse_output(
        self, addresses: list[int], output: str
    ) -> list[SymbolicatedFrame]:
        """Parse addr2line output (function\\nfile:line pairs)."""
        lines = output.splitlines()
        frames: list[SymbolicatedFrame] = []

        # addr2line -f outputs two lines per address: function, then file:line
        for i, addr in enumerate(addresses):
            base = i * 2
            if base + 1 >= len(lines):
                frames.append(self._make_frame(addr, SymbolicationStatus.PARSE_ERROR))
                continue

            func = lines[base].strip() or "??"
            loc = lines[base + 1].strip() or "??:0"

            if func == "??" and loc in ("??:0", "??:?", ""):
                status = SymbolicationStatus.NO_DEBUG_INFO
            else:
                status = SymbolicationStatus.OK

            frames.append(
                SymbolicatedFrame(
                    address=addr,
                    address_hex=hex(addr),
                    function_name=func,
                    source_location=loc,
                    status=status,
                )
            )

        return frames

    @staticmethod
    def _make_frame(addr: int, status: SymbolicationStatus) -> SymbolicatedFrame:
        return SymbolicatedFrame(
            address=addr,
            address_hex=hex(addr),
            function_name="??",
            source_location="??:0",
            status=status,
        )
