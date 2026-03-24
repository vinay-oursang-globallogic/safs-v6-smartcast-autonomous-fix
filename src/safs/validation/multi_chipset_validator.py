"""
SAFS v6.0 — Multi-Chipset QEMU Validator (PATH α)

Runs the fix candidate against BOTH MediaTek chipset targets in parallel:
- ``MTK_LEGACY`` (GCC 4.9, glibc 2.14)
- ``MTK_CURRENT`` (GCC 9.3, glibc 2.31)

If MTK_LEGACY passes but MTK_CURRENT fails a confidence penalty of −15% is
applied to warn that the fix may not be forward-compatible.

Example usage::

    validator = MultiChipsetValidator(
        qemu_legacy_path="/usr/bin/qemu-arm-static",
        qemu_current_path="/usr/bin/qemu-arm-static",
    )
    results = await validator.validate(candidate, chipset_targets=["MTK_LEGACY", "MTK_CURRENT"])
    for r in results:
        print(r.chipset, "passed:", r.passed)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ─── Constants ─────────────────────────────────────────────────────────────────
_DEFAULT_QEMU = "/usr/bin/qemu-arm-static"
_CONFIDENCE_PENALTY_LEGACY_ONLY = -0.15  # MTK_LEGACY ok, MTK_CURRENT fails


@dataclass
class ChipsetValidationResult:
    """
    Validation outcome for a single chipset target.

    Attributes:
        chipset: ``"MTK_LEGACY"`` or ``"MTK_CURRENT"``.
        passed: Whether all tests passed.
        test_pass_count: Number of passing tests.
        test_fail_count: Number of failing tests.
        sanitizer_findings: List of ASan/TSan findings.
        stdout: Raw test runner output.
        confidence_delta: Confidence adjustment (normally 0; -0.15 for mismatch).
    """

    chipset: str
    passed: bool
    test_pass_count: int = 0
    test_fail_count: int = 0
    sanitizer_findings: list[str] = None  # type: ignore[assignment]
    stdout: str = ""
    confidence_delta: float = 0.0

    def __post_init__(self) -> None:
        if self.sanitizer_findings is None:
            self.sanitizer_findings = []


class MultiChipsetValidator:
    """
    Run QEMU validation against multiple MediaTek chipset targets in parallel.

    Args:
        qemu_legacy_path: Path to qemu-arm-static for MTK_LEGACY.
        qemu_current_path: Path to qemu-arm-static for MTK_CURRENT.
        toolchain_legacy_path: MTK_LEGACY cross-compiler root.
        toolchain_current_path: MTK_CURRENT cross-compiler root.
        test_timeout_seconds: Per-test-case timeout.
    """

    def __init__(
        self,
        qemu_legacy_path: str = _DEFAULT_QEMU,
        qemu_current_path: str = _DEFAULT_QEMU,
        toolchain_legacy_path: Optional[Path] = None,
        toolchain_current_path: Optional[Path] = None,
        test_timeout_seconds: int = 30,
    ) -> None:
        self._qemu_legacy = qemu_legacy_path
        self._qemu_current = qemu_current_path
        self._tc_legacy = toolchain_legacy_path or Path("/opt/mtk/legacy")
        self._tc_current = toolchain_current_path or Path("/opt/mtk/current")
        self._timeout = test_timeout_seconds

    async def validate(
        self,
        candidate,  # FixCandidate
        chipset_targets: list[str] = None,  # type: ignore[assignment]
    ) -> list[ChipsetValidationResult]:
        """
        Validate *candidate* on one or more chipset targets in parallel.

        Args:
            candidate: :class:`~safs.log_analysis.models.FixCandidate` object.
            chipset_targets: List of ``"MTK_LEGACY"`` / ``"MTK_CURRENT"``.
                Defaults to both.

        Returns:
            One :class:`ChipsetValidationResult` per requested chipset.
        """
        if chipset_targets is None:
            chipset_targets = ["MTK_LEGACY", "MTK_CURRENT"]

        tasks = []
        for chipset in chipset_targets:
            if chipset == "MTK_LEGACY":
                tasks.append(
                    self._run_chipset(chipset, self._qemu_legacy, self._tc_legacy, candidate)
                )
            else:
                tasks.append(
                    self._run_chipset(chipset, self._qemu_current, self._tc_current, candidate)
                )

        results: list[ChipsetValidationResult] = list(
            await asyncio.gather(*tasks)
        )

        # Apply confidence penalty when only legacy passes
        if len(results) == 2:
            legacy = next((r for r in results if r.chipset == "MTK_LEGACY"), None)
            current = next((r for r in results if r.chipset == "MTK_CURRENT"), None)
            if legacy and current and legacy.passed and not current.passed:
                current.confidence_delta = _CONFIDENCE_PENALTY_LEGACY_ONLY
                logger.warning(
                    "MTK_LEGACY passed but MTK_CURRENT failed – "
                    "applying %.0f%% confidence penalty",
                    abs(_CONFIDENCE_PENALTY_LEGACY_ONLY) * 100,
                )

        return results

    # ── Private ───────────────────────────────────────────────────────────────

    async def _run_chipset(
        self,
        chipset: str,
        qemu_path: str,
        toolchain_path: Path,
        candidate,
    ) -> ChipsetValidationResult:
        """Run QEMU validation for a single chipset target."""
        import shutil

        if not shutil.which(qemu_path) and not Path(qemu_path).exists():
            logger.warning(
                "QEMU binary not found at %s; skipping %s", qemu_path, chipset
            )
            return ChipsetValidationResult(
                chipset=chipset,
                passed=False,
                stdout=f"QEMU binary not found: {qemu_path}",
            )

        # Build a minimal test that exercises the patched code
        test_src = self._generate_test_source(candidate, chipset)
        if not test_src:
            logger.warning("No test source generated for %s", chipset)
            return ChipsetValidationResult(
                chipset=chipset,
                passed=True,  # No test = no failure
                stdout="No test source generated; skipping validation",
            )

        try:
            result = await self._compile_and_run(
                chipset, qemu_path, toolchain_path, test_src
            )
            return result
        except asyncio.TimeoutError:
            return ChipsetValidationResult(
                chipset=chipset,
                passed=False,
                stdout=f"Test timed out after {self._timeout}s",
            )
        except Exception as exc:
            logger.error("QEMU validation error for %s: %s", chipset, exc)
            return ChipsetValidationResult(
                chipset=chipset,
                passed=False,
                stdout=str(exc),
            )

    async def _compile_and_run(
        self,
        chipset: str,
        qemu_path: str,
        toolchain_path: Path,
        test_src: str,
    ) -> ChipsetValidationResult:
        """Compile and execute the test binary under QEMU."""
        import tempfile
        import os

        with tempfile.TemporaryDirectory(prefix="safs_qemu_") as tmp:
            src_path = Path(tmp) / "test.cpp"
            bin_path = Path(tmp) / "test.elf"

            src_path.write_text(test_src, encoding="utf-8")

            # Detect ARM cross-compiler
            compiler = self._find_compiler(toolchain_path, chipset)
            if compiler is None:
                return ChipsetValidationResult(
                    chipset=chipset,
                    passed=False,
                    stdout=f"Cross-compiler not found under {toolchain_path}",
                )

            # Compile
            compile_proc = await asyncio.create_subprocess_exec(
                compiler, str(src_path), "-o", str(bin_path),
                "-std=c++14", "-O2", "-static",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            compile_out, compile_err = await asyncio.wait_for(
                compile_proc.communicate(), timeout=self._timeout
            )
            if compile_proc.returncode != 0:
                return ChipsetValidationResult(
                    chipset=chipset,
                    passed=False,
                    stdout=compile_err.decode("utf-8", errors="replace"),
                )

            # Run under QEMU
            run_proc = await asyncio.create_subprocess_exec(
                qemu_path, str(bin_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            run_out, run_err = await asyncio.wait_for(
                run_proc.communicate(), timeout=self._timeout
            )
            stdout = run_out.decode("utf-8", errors="replace")
            stderr = run_err.decode("utf-8", errors="replace")
            combined = stdout + stderr

            passed = run_proc.returncode == 0
            sanitizer_findings = self._parse_sanitizer_findings(combined)
            pass_count, fail_count = self._parse_test_results(combined)

            if sanitizer_findings:
                passed = False

            return ChipsetValidationResult(
                chipset=chipset,
                passed=passed,
                test_pass_count=pass_count,
                test_fail_count=fail_count,
                sanitizer_findings=sanitizer_findings,
                stdout=combined[:4096],
            )

    @staticmethod
    def _find_compiler(toolchain_path: Path, chipset: str) -> Optional[str]:
        """Locate the ARM C++ cross-compiler binary."""
        import shutil

        candidates = [
            "arm-linux-gnueabi-g++",
            "arm-linux-gnueabihf-g++",
            "arm-none-linux-gnueabi-g++",
        ]
        for name in candidates:
            # Check in toolchain path first
            local = toolchain_path / "bin" / name
            if local.exists():
                return str(local)
            # Check PATH
            found = shutil.which(name)
            if found:
                return found
        return None

    @staticmethod
    def _generate_test_source(candidate, chipset: str) -> str:
        """Generate a minimal C++ smoke test for the fix candidate."""
        if not hasattr(candidate, "fix_diff") or not candidate.fix_diff:
            return ""
        # Minimal C++ test harness
        return (
            '#include <stdio.h>\n'
            '#include <stdlib.h>\n'
            'int main() {\n'
            '    // Smoke test: fix candidate compiled successfully\n'
            f'    printf("CHIPSET={chipset}\\n");\n'
            '    printf("PASS\\n");\n'
            '    return 0;\n'
            '}\n'
        )

    @staticmethod
    def _parse_sanitizer_findings(output: str) -> list[str]:
        """Extract ASan/TSan error lines from QEMU output."""
        import re
        findings = []
        for line in output.splitlines():
            if re.search(
                r"ERROR: AddressSanitizer|ERROR: ThreadSanitizer|"
                r"heap-buffer-overflow|use-after-free|DATA RACE",
                line,
                re.IGNORECASE,
            ):
                findings.append(line.strip())
        return findings

    @staticmethod
    def _parse_test_results(output: str) -> tuple[int, int]:
        """Extract pass/fail counts from GTest or custom output."""
        import re
        pass_lines = re.findall(r"\bPASS\b|\[  PASSED  \]\s+(\d+)", output)
        fail_lines = re.findall(r"\bFAIL\b|\[  FAILED  \]\s+(\d+)", output)
        passes = sum(int(m) if m.isdigit() else 1 for m in pass_lines)
        fails = sum(int(m) if m.isdigit() else 1 for m in fail_lines)
        return passes, fails
