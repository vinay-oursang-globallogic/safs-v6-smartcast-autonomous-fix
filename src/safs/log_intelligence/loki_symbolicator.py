"""
SAFS v6.0 - LOKi Native Symbolicator

ASLR-corrected addr2line symbolication for LOKi C++ crashes.

**Symbolication Pipeline**:
1. Extract /proc/pid/maps from crash log (ASLR load addresses)
2. Extract backtrace frames (virtual PC addresses)
3. For each frame:
   - Correct PC for ASLR: file_offset = virtual_pc - library_load_base
   - Look up debug ELF by Build-ID
   - Run addr2line with file_offset
4. Return symbolicated frames with function:file:line

**Build-ID ELF Lookup**:
- Debug ELF binaries stored in symbol server (local or remote)
- Build-ID extracted from ELF: `readelf -n binary | grep "Build ID"`
- Symbol store organized: `/symbols/<build-id[:2]>/<build-id[2:]>/binary.debug`

**Example Crash Log**:
```
fatal signal 11 (SIGSEGV), fault addr 0x00000000
/proc/12345/maps:
  7f8a4000-7f8b2000 r-xp 00000000 /3rd/loki/lib/libloki_core.so
Backtrace:
  #0 pc 000051a4 libloki_core.so (_ZN4Loki11AppLauncher5LaunchEv+52)
```

After ASLR correction:
  virtual_pc = 0x7f8a51a4
  library_load_base = 0x7f8a4000
  file_offset = 0x51a4
  addr2line -e libloki_core.debug 0x51a4
  → AppLauncher.cpp:142 Loki::AppLauncher::Launch()
"""

import asyncio
import hashlib
import re
import subprocess
from pathlib import Path
from typing import Optional

from .models import (
    BacktraceFrame,
    LoadMapEntry,
    LokiSymbolicationResult,
    SymbolicatedFrame,
)


# ==================================================================================
# LOAD MAP PARSER (/proc/pid/maps)
# ==================================================================================


class LoadMapParser:
    """Parses /proc/pid/maps from crash logs"""

    # Regex for /proc/pid/maps line:
    # 7f8a4000-7f8b2000 r-xp 00000000 08:01 12345 /3rd/loki/lib/libloki_core.so
    MAP_PATTERN = re.compile(
        r"([0-9a-f]+)-([0-9a-f]+)\s+([rwxp-]+)\s+[0-9a-f]+\s+[\w:]+\s+\d+\s+(.+)"
    )

    @staticmethod
    def parse(log_lines: list[str]) -> list[LoadMapEntry]:
        """
        Extract /proc/pid/maps from crash log.

        Args:
            log_lines: Crash log lines (must contain /proc/pid/maps section)

        Returns:
            List of LoadMapEntry (parsed memory mappings)
        """
        entries = []
        in_maps_section = False

        for line in log_lines:
            # Start of maps section
            if "/proc/" in line and "/maps" in line:
                in_maps_section = True
                continue

            # End of maps section (blank line or new section)
            if in_maps_section and (line.strip() == "" or line.startswith("Backtrace")):
                break

            # Parse map line
            if in_maps_section:
                match = LoadMapParser.MAP_PATTERN.search(line)
                if match:
                    load_address = int(match.group(1), 16)
                    end_address = int(match.group(2), 16)
                    permissions = match.group(3)
                    path = match.group(4).strip()

                    # Only keep executable sections (r-xp)
                    if "x" in permissions:
                        library_name = Path(path).name
                        entries.append(
                            LoadMapEntry(
                                library_name=library_name,
                                load_address=load_address,
                                end_address=end_address,
                                permissions=permissions,
                            )
                        )

        return entries


# ==================================================================================
# BACKTRACE PARSER
# ==================================================================================


class BacktraceParser:
    """Parses backtrace from LOKi crash logs"""

    # Regex for backtrace frame:
    # "#0 pc 000051a4 libloki_core.so (_ZN4Loki11AppLauncher5LaunchEv+52)"
    # "#1 pc 00007f8a51a4 libloki_ui.so"
    FRAME_PATTERN = re.compile(
        r"#(\d+)\s+pc\s+([0-9a-f]+)\s+(\S+)(?:\s+\(([^)]+)\))?"
    )

    @staticmethod
    def parse(log_lines: list[str]) -> list[BacktraceFrame]:
        """
        Extract backtrace frames from crash log.

        Args:
            log_lines: Crash log lines

        Returns:
            List of BacktraceFrame (frame number, library, PC address)
        """
        frames = []

        for line in log_lines:
            match = BacktraceParser.FRAME_PATTERN.search(line)
            if match:
                frame_number = int(match.group(1))
                virtual_pc = int(match.group(2), 16)
                library_name = match.group(3)
                # group(4) is optional mangled symbol (not used for symbolication)

                frames.append(
                    BacktraceFrame(
                        frame_number=frame_number,
                        library_name=library_name,
                        virtual_pc=virtual_pc,
                        build_id=None,  # TODO: extract from crash log if present
                    )
                )

        return frames


# ==================================================================================
# SYMBOL STORE
# ==================================================================================


class SymbolStore:
    """
    Manages debug ELF symbol storage.

    Layout:
      /symbols/<build-id[:2]>/<build-id[2:]>/binary.debug

    Example:
      Build-ID: a1b2c3d4e5f6...
      Path: /symbols/a1/b2c3d4e5f6.../libloki_core.so.debug
    """

    def __init__(self, symbol_root: Path):
        """
        Initialize symbol store.

        Args:
            symbol_root: Root directory for debug symbols (e.g., /opt/safs/symbols)
        """
        self.symbol_root = symbol_root
        if not self.symbol_root.exists():
            self.symbol_root.mkdir(parents=True, exist_ok=True)

    def find_by_build_id(self, build_id: str) -> Optional[Path]:
        """
        Find debug ELF by Build-ID.

        Args:
            build_id: ELF Build-ID (hex string)

        Returns:
            Path to debug ELF, or None if not found
        """
        if not build_id or len(build_id) < 4:
            return None

        # Build path: /symbols/a1/b2c3d4e5f6.../*.debug
        subdir = self.symbol_root / build_id[:2] / build_id[2:]
        if not subdir.exists():
            return None

        # Find first .debug file in subdir
        debug_files = list(subdir.glob("*.debug"))
        if debug_files:
            return debug_files[0]

        return None

    def find_by_library_name(self, library_name: str) -> Optional[Path]:
        """
        Fallback: find debug ELF by library name (when Build-ID unavailable).

        Args:
            library_name: Library filename (e.g., "libloki_core.so")

        Returns:
            Path to debug ELF, or None if not found
        """
        # Search entire symbol tree for matching filename
        debug_name = f"{library_name}.debug"
        matches = list(self.symbol_root.rglob(debug_name))
        if matches:
            return matches[0]  # Return first match

        return None


# ==================================================================================
# ADDR2LINE SYMBOLICATION
# ==================================================================================


class Addr2LineSymbolicator:
    """Runs addr2line to symbolicate addresses"""

    def __init__(self, addr2line_path: str = "addr2line"):
        """
        Initialize symbolicator.

        Args:
            addr2line_path: Path to addr2line binary (default: "addr2line" in PATH)
        """
        self.addr2line = addr2line_path

    async def symbolicate(
        self, debug_elf: Path, file_offset: int
    ) -> tuple[Optional[str], Optional[str], Optional[int]]:
        """
        Symbolicate a file offset using addr2line.

        Args:
            debug_elf: Path to debug ELF binary
            file_offset: File offset (ASLR-corrected)

        Returns:
            Tuple of (function_name, file_name, line_number) or (None, None, None)
        """
        try:
            # Run addr2line -f -e <elf> <offset>
            # -f: show function name
            # -e: specify ELF file
            result = await asyncio.create_subprocess_exec(
                self.addr2line,
                "-f",
                "-e",
                str(debug_elf),
                f"0x{file_offset:x}",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            stdout, stderr = await result.communicate()

            if result.returncode != 0:
                return (None, None, None)

            # Parse output:
            # Line 1: function name (or "??" if unknown)
            # Line 2: file:line (or "??:0" if unknown)
            lines = stdout.decode().strip().split("\n")
            if len(lines) < 2:
                return (None, None, None)

            function_name = lines[0].strip()
            file_line = lines[1].strip()

            # Parse file:line
            if ":" in file_line:
                file_name, line_str = file_line.rsplit(":", 1)
                try:
                    line_number = int(line_str)
                except ValueError:
                    line_number = None
            else:
                file_name = None
                line_number = None

            # Check for "??" unknown placeholders
            if function_name == "??":
                function_name = None
            if file_name == "??":
                file_name = None
            if line_number == 0:
                line_number = None

            return (function_name, file_name, line_number)

        except Exception:
            return (None, None, None)


# ==================================================================================
# LOKI SYMBOLICATOR (MAIN CLASS)
# ==================================================================================


class LokiSymbolicator:
    """
    Main LOKi symbolication orchestrator.

    Combines load map parsing, backtrace parsing, ASLR correction, and addr2line.
    """

    def __init__(
        self,
        symbol_store: SymbolStore,
        addr2line_symbolicator: Optional[Addr2LineSymbolicator] = None,
    ):
        """
        Initialize symbolicator.

        Args:
            symbol_store: SymbolStore for debug ELF lookup
            addr2line_symbolicator: Optional custom Addr2LineSymbolicator
        """
        self.symbol_store = symbol_store
        self.addr2line = addr2line_symbolicator or Addr2LineSymbolicator()

    async def symbolicate(self, log_lines: list[str]) -> LokiSymbolicationResult:
        """
        Symbolicate LOKi crash log.

        Args:
            log_lines: Crash log lines (must contain /proc/pid/maps and backtrace)

        Returns:
            LokiSymbolicationResult with symbolicated frames
        """
        # Step 1: Parse load map
        load_map = LoadMapParser.parse(log_lines)

        # Step 2: Parse backtrace
        raw_frames = BacktraceParser.parse(log_lines)

        # Step 3: Build library load address lookup
        load_address_map = {
            entry.library_name: entry.load_address for entry in load_map
        }

        # Step 4: Symbolicate each frame
        symbolicated_frames = []
        for frame in raw_frames:
            symbolicated = await self._symbolicate_frame(
                frame, load_address_map
            )
            symbolicated_frames.append(symbolicated)

        # Step 5: Calculate success rate
        success_count = sum(
            1 for f in symbolicated_frames if f.status == "OK"
        )
        success_rate = (
            success_count / len(symbolicated_frames)
            if symbolicated_frames
            else 0.0
        )

        return LokiSymbolicationResult(
            load_map=load_map,
            raw_frames=raw_frames,
            symbolicated_frames=symbolicated_frames,
            symbolication_success_rate=success_rate,
        )

    async def _symbolicate_frame(
        self, frame: BacktraceFrame, load_address_map: dict[str, int]
    ) -> SymbolicatedFrame:
        """Symbolicate a single backtrace frame"""

        # Check if we have load address for this library
        if frame.library_name not in load_address_map:
            return SymbolicatedFrame(
                frame_number=frame.frame_number,
                library_name=frame.library_name,
                virtual_pc=frame.virtual_pc,
                file_offset=None,
                function_name=None,
                file_name=None,
                line_number=None,
                status="ASLR_UNKNOWN",
            )

        # ASLR correction: file_offset = virtual_pc - library_load_base
        library_load_base = load_address_map[frame.library_name]
        file_offset = frame.virtual_pc - library_load_base

        # Find debug ELF
        debug_elf = None
        if frame.build_id:
            debug_elf = self.symbol_store.find_by_build_id(frame.build_id)
        if not debug_elf:
            # Fallback to library name lookup
            debug_elf = self.symbol_store.find_by_library_name(frame.library_name)

        if not debug_elf:
            return SymbolicatedFrame(
                frame_number=frame.frame_number,
                library_name=frame.library_name,
                virtual_pc=frame.virtual_pc,
                file_offset=file_offset,
                function_name=None,
                file_name=None,
                line_number=None,
                status="NO_DEBUG_ELF",
            )

        # Run addr2line
        function_name, file_name, line_number = await self.addr2line.symbolicate(
            debug_elf, file_offset
        )

        if function_name is None and file_name is None:
            status = "ADDR2LINE_FAIL"
        else:
            status = "OK"

        return SymbolicatedFrame(
            frame_number=frame.frame_number,
            library_name=frame.library_name,
            virtual_pc=frame.virtual_pc,
            file_offset=file_offset,
            function_name=function_name,
            file_name=file_name,
            line_number=line_number,
            status=status,
        )
