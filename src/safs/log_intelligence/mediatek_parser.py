"""
SAFS v6.0 - MediaTek Kernel Parser

Parses Linux kernel oops/panic logs from MediaTek SoC Smart TVs.

**Supported Error Types**:
- NULL pointer dereference
- Page fault (read/write)
- Kernel panic
- BUG() assertions
- Oops in interrupt context

**MediaTek Subsystem Classification**:
- VDEC: Video decoder (H.264/HEVC/VP9/AV1 hardware)
- TRUSTZONE: Widevine L1 TrustZone TEE
- MALI_GPU: ARM Mali GPU driver
- HDMI: HDMI output driver
- IR_INPUT: IR remote input driver (/dev/input/event*)
- DIRECTFB: DirectFB graphics
- MTK_FIRMWARE: Generic MTK firmware/BSP

**Example Kernel Oops**:
```
[  417.695436] Unable to handle kernel NULL pointer dereference at virtual address 00000000
[  417.695512] pgd = c0004000
[  417.695582] [00000000] *pgd=00000000
[  417.695681] Internal error: Oops: 5 [#1] SMP ARM
[  417.695751] CPU: 0 PID: 1234 Comm: loki Tainted: G           O    4.9.118 #1
[  417.695821] Hardware name: MT5882 Board
[  417.695891] task: ee123400 task.stack: ee124000
[  417.695961] PC is at 0x7f8a51a4
[  417.696031] LR is at mtk_vdec_decode+0x48/0x120 [mtk_vdec]
[  417.696101] pc : [<7f8a51a4>]    lr : [<7f8b6234>]
[  417.696171] Call Trace:
[  417.696241]  [<7f8b6234>] mtk_vdec_decode+0x48/0x120 [mtk_vdec]
[  417.696311]  [<7f8c1234>] vdec_thread+0x12c/0x200 [mtk_vdec]
```

**Auto-Escalation Rules**:
- All MediaTek kernel errors → hw_triage queue
- SAFS NEVER generates kernel patches
- Analysis comment added to Jira with subsystem classification
"""

import re
from datetime import datetime, timezone
from typing import Optional

from .models import KernelOops, MediaTekKernelResult


# ==================================================================================
# KERNEL OOPS PARSER
# ==================================================================================


class KernelOopsParser:
    """Parses Linux kernel oops/panic from dmesg logs"""

    # Oops signature patterns
    OOPS_PATTERNS = {
        "NULL_DEREF": re.compile(
            r"Unable to handle kernel NULL pointer dereference at virtual address ([0-9a-f]+)",
            re.IGNORECASE,
        ),
        "PAGE_FAULT": re.compile(
            r"BUG: unable to handle kernel paging request at ([0-9a-f]+)",
            re.IGNORECASE,
        ),
        "KERNEL_PANIC": re.compile(r"Kernel panic - not syncing:(.+)", re.IGNORECASE),
        "BUG": re.compile(r"kernel BUG at (.+):(\d+)!", re.IGNORECASE),
        "OOPS": re.compile(r"Internal error: Oops:(.+)", re.IGNORECASE),
    }

    # Instruction pointer patterns
    PC_PATTERNS = [
        re.compile(r"PC is at (.+)\+0x([0-9a-f]+)/0x([0-9a-f]+)", re.IGNORECASE),
        re.compile(r"pc : \[<([0-9a-f]+)>\]", re.IGNORECASE),
        re.compile(r"RIP: (.+)\+0x([0-9a-f]+)/0x([0-9a-f]+)", re.IGNORECASE),
    ]

    # Tainted kernel flag
    TAINTED_PATTERN = re.compile(r"Tainted:\s*([A-Z\s]+)", re.IGNORECASE)

    # Call trace start
    CALL_TRACE_START = re.compile(r"Call [Tt]race:|Backtrace:", re.IGNORECASE)

    # Call trace line (various formats)
    CALL_TRACE_LINE = re.compile(
        r"(?:\[<[0-9a-f]+>\])?\s*([a-zA-Z0-9_\.]+)\+0x([0-9a-f]+)/0x([0-9a-f]+)",
        re.IGNORECASE,
    )

    def parse(self, log_lines: list[str]) -> list[KernelOops]:
        """
        Parse kernel oops/panic from log lines.

        Args:
            log_lines: Kernel log lines (dmesg format)

        Returns:
            List of KernelOops
        """
        oops_list = []
        i = 0

        while i < len(log_lines):
            line = log_lines[i]

            # Detect oops start
            oops_type = None
            faulting_address = None

            for oops_name, pattern in self.OOPS_PATTERNS.items():
                match = pattern.search(line)
                if match:
                    oops_type = oops_name
                    # Extract faulting address if present
                    if match.lastindex and match.lastindex >= 1:
                        try:
                            faulting_address = int(match.group(1), 16)
                        except (ValueError, IndexError):
                            pass
                    break

            if oops_type:
                # Parse full oops (next ~20 lines)
                oops = self._parse_oops_block(
                    log_lines[i : i + 50], oops_type, faulting_address
                )
                if oops:
                    oops_list.append(oops)
                    # Skip ahead based on call trace length to avoid re-parsing same oops
                    # but not so much that we miss nearby oops
                    skip_lines = min(5 + len(oops.call_trace), 15)
                    i += skip_lines
                    continue

            i += 1

        return oops_list

    def _parse_oops_block(
        self, block_lines: list[str], oops_type: str, faulting_address: Optional[int]
    ) -> Optional[KernelOops]:
        """Parse a full oops block (20-50 lines)"""
        timestamp = self._extract_timestamp(block_lines[0])
        instruction_pointer = self._extract_pc(block_lines)
        tainted = self._is_tainted(block_lines)
        call_trace = self._extract_call_trace(block_lines)

        # For KERNEL_PANIC, PC is optional (panic might be triggered without full stack trace)
        if instruction_pointer is None and oops_type not in ("KERNEL_PANIC", "BUG"):
            # Can't parse oops without PC (except for panic/bug)
            return None

        return KernelOops(
            timestamp=timestamp,
            oops_type=oops_type,
            faulting_address=faulting_address,
            instruction_pointer=instruction_pointer or 0,  # Default to 0 if not found
            call_trace=call_trace,
            tainted=tainted,
            subsystem=None,  # Will be classified separately
        )

    def _extract_timestamp(self, line: str) -> datetime:
        """Extract kernel timestamp [  417.695436]"""
        match = re.search(r"\[\s*(\d+\.\d+)\]", line)
        if match:
            uptime_seconds = float(match.group(1))
            # Use current time - uptime as approximate timestamp
            # (proper implementation needs log file mtime)
            return datetime.now(timezone.utc)
        return datetime.now(timezone.utc)

    def _extract_pc(self, block_lines: list[str]) -> Optional[int]:
        """Extract instruction pointer (PC/RIP)"""
        for line in block_lines:
            for pattern in self.PC_PATTERNS:
                match = pattern.search(line)
                if match:
                    # Extract hex address
                    # Pattern 1: "PC is at func+0x123"
                    if "PC is at" in line or "RIP:" in line:
                        # Extract offset from function (not absolute PC)
                        # Skip this pattern for now
                        continue
                    # Pattern 2: "pc : [<7f8a51a4>]"
                    pc_str = match.group(1)
                    try:
                        return int(pc_str, 16)
                    except ValueError:
                        pass
        return None

    def _is_tainted(self, block_lines: list[str]) -> bool:
        """Check if kernel is tainted"""
        for line in block_lines:
            match = self.TAINTED_PATTERN.search(line)
            if match:
                tainted_flags = match.group(1).strip()
                # Tainted if flags not empty (excluding whitespace)
                return bool(tainted_flags.replace(" ", ""))
        return False

    def _extract_call_trace(self, block_lines: list[str]) -> list[str]:
        """Extract call trace"""
        call_trace = []
        in_call_trace = False

        for line in block_lines:
            if self.CALL_TRACE_START.search(line):
                in_call_trace = True
                continue

            if in_call_trace:
                # End of call trace (blank line, end trace marker, or new oops signature)
                if line.strip() == "" or "---" in line or "Unable to handle" in line:
                    break

                # Parse call trace line
                match = self.CALL_TRACE_LINE.search(line)
                if match:
                    function_name = match.group(1)
                    offset = match.group(2)
                    call_trace.append(f"{function_name}+0x{offset}")
                elif line.strip():
                    # Fallback: add raw line if it looks like a trace line
                    call_trace.append(line.strip())

        return call_trace


# ==================================================================================
# MEDIATEK SUBSYSTEM CLASSIFIER
# ==================================================================================


class MediaTekSubsystemClassifier:
    """Classifies kernel errors by MediaTek subsystem"""

    # Subsystem detection patterns (function names, module names, keywords)
    SUBSYSTEM_PATTERNS = {
        "VDEC": [
            "mtk_vdec",
            "vdec_",
            "h264_decode",
            "hevc_decode",
            "vp9_decode",
            "av1_decode",
            "video_decoder",
        ],
        "TRUSTZONE": [
            "trustzone",
            "tee_",
            "widevine",
            "optee",
            "tzdriver",
            "smc_call",
        ],
        "MALI_GPU": [
            "mali",
            "kbase",
            "gpu_",
            "mali_kbase",
            "mali_gpu",
        ],
        "HDMI": [
            "hdmi",
            "hdmitx",
            "hdcp",
            "edid",
            "cec",
        ],
        "IR_INPUT": [
            "ir_",
            "input_event",
            "mtk_ir",
            "remote_control",
            "/dev/input",
        ],
        "DIRECTFB": [
            "directfb",
            "dfb_",
            "surface_",
            "gfx_",
        ],
        "MTK_FIRMWARE": [
            "mtk_",
            "mediatek",
            "bsp_",
        ],
    }

    def classify(self, oops: KernelOops) -> str:
        """
        Classify oops by MediaTek subsystem.

        Args:
            oops: Parsed KernelOops

        Returns:
            Subsystem name (e.g., "VDEC", "MALI_GPU") or "UNKNOWN"
        """
        # Check call trace for subsystem keywords
        for subsystem, keywords in self.SUBSYSTEM_PATTERNS.items():
            for trace_line in oops.call_trace:
                trace_lower = trace_line.lower()
                for keyword in keywords:
                    if keyword.lower() in trace_lower:
                        return subsystem

        return "UNKNOWN"


# ==================================================================================
# HARDWARE ERROR DETECTOR
# ==================================================================================


class HardwareErrorDetector:
    """Detects hardware-level errors that require hw_triage escalation"""

    # Hardware error patterns (unrecoverable hardware issues)
    HARDWARE_ERROR_PATTERNS = [
        re.compile(r"hardware error", re.IGNORECASE),
        re.compile(r"machine check exception", re.IGNORECASE),
        re.compile(r"CPU \d+ stuck", re.IGNORECASE),
        re.compile(r"watchdog timeout", re.IGNORECASE),
        re.compile(r"i2c transfer failed", re.IGNORECASE),
        re.compile(r"ddr.*ecc error", re.IGNORECASE),
        re.compile(r"thermal emergency", re.IGNORECASE),
        re.compile(r"power supply.*fail", re.IGNORECASE),
    ]

    def detect(self, log_lines: list[str]) -> list[str]:
        """
        Detect hardware errors in kernel logs.

        Args:
            log_lines: Kernel log lines

        Returns:
            List of hardware error descriptions
        """
        errors = []

        for line in log_lines:
            for pattern in self.HARDWARE_ERROR_PATTERNS:
                if pattern.search(line):
                    errors.append(line.strip())
                    break

        return errors


# ==================================================================================
# MEDIATEK KERNEL ANALYZER (MAIN CLASS)
# ==================================================================================


class MediaTekKernelAnalyzer:
    """
    Main MediaTek kernel log analyzer.

    Combines oops parsing, subsystem classification, and hardware error detection.
    """

    def __init__(self):
        self.oops_parser = KernelOopsParser()
        self.subsystem_classifier = MediaTekSubsystemClassifier()
        self.hardware_detector = HardwareErrorDetector()

    def analyze(self, log_lines: list[str]) -> MediaTekKernelResult:
        """
        Analyze MediaTek kernel logs.

        Args:
            log_lines: Kernel log lines (dmesg format)

        Returns:
            MediaTekKernelResult with oops list, hardware errors, subsystem classification
        """
        # Parse kernel oopses
        oops_list = self.oops_parser.parse(log_lines)

        # Classify each oops by subsystem
        for oops in oops_list:
            oops.subsystem = self.subsystem_classifier.classify(oops)

        # Count errors by subsystem
        subsystem_counts = {}
        for oops in oops_list:
            subsystem = oops.subsystem or "UNKNOWN"
            subsystem_counts[subsystem] = subsystem_counts.get(subsystem, 0) + 1

        # Detect hardware errors
        hardware_errors = self.hardware_detector.detect(log_lines)

        return MediaTekKernelResult(
            oops_list=oops_list,
            hardware_errors=hardware_errors,
            subsystem_classification=subsystem_counts,
        )
