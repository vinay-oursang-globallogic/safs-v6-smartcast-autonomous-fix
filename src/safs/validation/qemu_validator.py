"""
SAFS v6.0 — QEMU ARM Validator (PATH α)

Cross-compiles LOKi C++ fixes for MTK_LEGACY and MTK_CURRENT toolchains,
runs unit tests in qemu-arm-static with AddressSanitizer/ThreadSanitizer.

Validation Steps:
1. Cross-compile with MTK_LEGACY (GCC 4.9, glibc 2.14)
2. Cross-compile with MTK_CURRENT (GCC 9.3, glibc 2.31)
3. Run unit tests in QEMU with ASan/TSan instrumentation
4. Verify zero sanitizer findings
5. Return pass/fail with detailed evidence

Limitations:
- Cannot test full UI rendering (DirectFB/OpenGL ES)
- Cannot test IPC with Chromium
- Cannot test input routing
- Fast feedback (~30s per test)
"""

import asyncio
import logging
import os
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..log_analysis.models import BugLayer, ErrorCategory, FixCandidate
from .models import (
    ChipsetTarget,
    PathValidationResult,
    QEMUValidationResult,
    SanitizerType,
    ValidationPath,
)

logger = logging.getLogger(__name__)


class QEMUValidator:
    """
    PATH α: QEMU ARM cross-compile + ASan/TSan validation.
    
    Validates LOKi C++ fixes by:
    1. Cross-compiling for both MTK chipset targets
    2. Running unit tests in qemu-arm-static
    3. Checking for sanitizer violations
    """
    
    def __init__(
        self,
        legacy_toolchain_path: Optional[Path] = None,
        current_toolchain_path: Optional[Path] = None,
        qemu_arm_path: str = "qemu-arm-static",
    ):
        """
        Initialize QEMU validator.
        
        Args:
            legacy_toolchain_path: Path to MTK_LEGACY GCC 4.9 toolchain
            current_toolchain_path: Path to MTK_CURRENT GCC 9.3 toolchain
            qemu_arm_path: Path to qemu-arm-static binary
        """
        self.legacy_toolchain = legacy_toolchain_path or Path("/opt/mtk/legacy")
        self.current_toolchain = current_toolchain_path or Path("/opt/mtk/current")
        self.qemu_arm = qemu_arm_path
        
    async def validate(
        self,
        candidate: FixCandidate,
        error_category: ErrorCategory,
        chipset_targets: Optional[List[ChipsetTarget]] = None,
        sanitizers: Optional[List[SanitizerType]] = None,
    ) -> PathValidationResult:
        """
        Validate a LOKi C++ fix candidate using QEMU.
        
        Args:
            candidate: Fix candidate to validate
            error_category: Error category (determines sanitizer choice)
            chipset_targets: Chipset targets to compile for (default: both)
            sanitizers: Sanitizers to run (default: ASan, TSan for races)
            
        Returns:
            PathValidationResult with QEMU validation results
        """
        start_time = asyncio.get_event_loop().time()
        
        # Default: test both chipsets
        if chipset_targets is None:
            chipset_targets = [ChipsetTarget.MTK_LEGACY, ChipsetTarget.MTK_CURRENT]
            
        # Default sanitizers: ASan always, TSan for race conditions
        if sanitizers is None:
            sanitizers = [SanitizerType.ASAN]
            if error_category == ErrorCategory.LOKI_RACE_CONDITION:
                sanitizers.append(SanitizerType.TSAN)
        
        logger.info(f"Starting QEMU validation for candidate {candidate.fix_id}")
        logger.info(f"Chipset targets: {chipset_targets}")
        logger.info(f"Sanitizers: {sanitizers}")
        
        # Compile and test
        qemu_results = QEMUValidationResult()
        test_results: Dict[str, bool] = {}
        evidence: Dict[str, str] = {}
        failure_reasons: List[str] = []
        
        try:
            # Test each chipset target
            for chipset in chipset_targets:
                compile_result = await self._cross_compile(
                    candidate, chipset, sanitizers
                )
                
                if not compile_result["success"]:
                    failure_reasons.append(
                        f"{chipset.value} compilation failed: {compile_result['error']}"
                    )
                    test_results[f"{chipset.value}_compile"] = False
                    evidence[f"{chipset.value}_compile_log"] = compile_result["log"]
                    continue
                
                # Run unit tests in QEMU
                test_result = await self._run_qemu_tests(
                    compile_result["binary_path"],
                    chipset,
                    sanitizers,
                )
                
                # Store results
                test_passed = test_result["passed"] and len(test_result["sanitizer_findings"]) == 0
                test_results[f"{chipset.value}_tests"] = test_passed
                evidence[f"{chipset.value}_output"] = test_result["output"]
                
                if chipset == ChipsetTarget.MTK_LEGACY:
                    qemu_results.mtk_legacy_passed = test_passed
                elif chipset == ChipsetTarget.MTK_CURRENT:
                    qemu_results.mtk_current_passed = test_passed
                
                # Accumulate sanitizer findings
                qemu_results.sanitizer_findings.extend(test_result["sanitizer_findings"])
                
                if not test_passed:
                    failure_reasons.extend(test_result["failures"])
                    
            # Store detailed results
            qemu_results.unit_test_output = evidence.get("MTK_CURRENT_output", "")
            qemu_results.compilation_logs = {
                k.replace("_compile_log", ""): v 
                for k, v in evidence.items() 
                if k.endswith("_compile_log")
            }
            
            # Overall pass: both chipsets passed + no sanitizer findings
            overall_passed = (
                all(test_results.values()) and 
                len(qemu_results.sanitizer_findings) == 0
            )
            
            # Apply confidence penalty if only MTK_CURRENT passed
            if (qemu_results.mtk_current_passed and 
                not qemu_results.mtk_legacy_passed and
                ChipsetTarget.MTK_LEGACY in chipset_targets):
                failure_reasons.append(
                    "MTK_LEGACY failed (confidence penalty: -15%)"
                )
                
        except Exception as e:
            logger.error(f"QEMU validation error: {e}", exc_info=True)
            failure_reasons.append(f"QEMU validation exception: {str(e)}")
            overall_passed = False
            
        duration = asyncio.get_event_loop().time() - start_time
        
        return PathValidationResult(
            path=ValidationPath.ALPHA_QEMU,
            passed=overall_passed,
            test_results=test_results,
            evidence={
                "qemu_details": qemu_results.model_dump(),
                **evidence,
            },
            failure_reasons=failure_reasons,
            duration_seconds=duration,
        )
    
    async def _cross_compile(
        self,
        candidate: FixCandidate,
        chipset: ChipsetTarget,
        sanitizers: List[SanitizerType],
    ) -> Dict[str, any]:
        """
        Cross-compile the fix for specified chipset target.
        
        Args:
            candidate: Fix candidate
            chipset: Chipset target
            sanitizers: Sanitizers to enable
            
        Returns:
            Dict with success, binary_path, log, error
        """
        # Select toolchain
        if chipset == ChipsetTarget.MTK_LEGACY:
            toolchain = self.legacy_toolchain
            gcc_version = "4.9"
        else:
            toolchain = self.current_toolchain
            gcc_version = "9.3"
        
        # Check toolchain availability
        gcc_path = toolchain / "bin" / "arm-linux-gnueabihf-g++"
        if not gcc_path.exists():
            return {
                "success": False,
                "log": "",
                "error": f"Toolchain not found: {gcc_path}",
            }
        
        # Build compiler flags
        cflags = ["-std=c++14", "-O2", "-Wall", "-Werror"]
        for sanitizer in sanitizers:
            cflags.append(f"-fsanitize={sanitizer.value}")
        
        # Create temporary build directory
        with tempfile.TemporaryDirectory() as tmpdir:
            build_dir = Path(tmpdir)
            
            # Write fixed source files
            for file_change in candidate.file_changes:
                src_path = build_dir / file_change.get("path", "main.cpp")
                src_path.parent.mkdir(parents=True, exist_ok=True)
                src_path.write_text(file_change.get("content", ""))
            
            # Compile command
            output_binary = build_dir / "test_binary"
            compile_cmd = [
                str(gcc_path),
                *cflags,
                "-o", str(output_binary),
                *[str(f) for f in build_dir.glob("**/*.cpp")],
            ]
            
            logger.debug(f"Compile command: {' '.join(compile_cmd)}")
            
            try:
                # Run compilation
                proc = await asyncio.create_subprocess_exec(
                    *compile_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=build_dir,
                )
                
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60.0)
                compile_log = stdout.decode(errors="replace")
                
                if proc.returncode != 0:
                    return {
                        "success": False,
                        "log": compile_log,
                        "error": f"Compilation failed with code {proc.returncode}",
                    }
                
                # Copy binary to persistent location
                output_path = Path(tempfile.mktemp(suffix=f"_{chipset.value}"))
                output_path.write_bytes(output_binary.read_bytes())
                
                return {
                    "success": True,
                    "binary_path": output_path,
                    "log": compile_log,
                    "error": None,
                }
                
            except asyncio.TimeoutError:
                return {
                    "success": False,
                    "log": "",
                    "error": "Compilation timeout (60s)",
                }
            except Exception as e:
                return {
                    "success": False,
                    "log": "",
                    "error": f"Compilation exception: {str(e)}",
                }
    
    async def _run_qemu_tests(
        self,
        binary_path: Path,
        chipset: ChipsetTarget,
        sanitizers: List[SanitizerType],
    ) -> Dict[str, any]:
        """
        Run unit tests in QEMU with sanitizer instrumentation.
        
        Args:
            binary_path: Compiled ARM binary
            chipset: Chipset target
            sanitizers: Enabled sanitizers
            
        Returns:
            Dict with passed, output, sanitizer_findings, failures
        """
        # QEMU command
        qemu_cmd = [self.qemu_arm, str(binary_path)]
        
        logger.debug(f"QEMU command: {' '.join(qemu_cmd)}")
        
        try:
            # Run in QEMU
            proc = await asyncio.create_subprocess_exec(
                *qemu_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env={
                    **os.environ,
                    "ASAN_OPTIONS": "detect_leaks=1:halt_on_error=0",
                    "TSAN_OPTIONS": "halt_on_error=0:second_deadlock_stack=1",
                },
            )
            
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120.0)
            output = stdout.decode(errors="replace")
            
            # Parse sanitizer findings
            sanitizer_findings = self._parse_sanitizer_output(output)
            
            # Test passed if exit code 0 and no sanitizer findings
            passed = proc.returncode == 0 and len(sanitizer_findings) == 0
            
            failures = []
            if proc.returncode != 0:
                failures.append(f"Test exit code: {proc.returncode}")
            if sanitizer_findings:
                failures.append(f"Sanitizer violations: {len(sanitizer_findings)}")
            
            return {
                "passed": passed,
                "output": output,
                "sanitizer_findings": sanitizer_findings,
                "failures": failures,
            }
            
        except asyncio.TimeoutError:
            return {
                "passed": False,
                "output": "",
                "sanitizer_findings": [],
                "failures": ["QEMU test timeout (120s)"],
            }
        except Exception as e:
            return {
                "passed": False,
                "output": "",
                "sanitizer_findings": [],
                "failures": [f"QEMU execution exception: {str(e)}"],
            }
    
    def _parse_sanitizer_output(self, output: str) -> List[str]:
        """
        Parse AddressSanitizer/ThreadSanitizer output for violations.
        
        Args:
            output: QEMU/sanitizer output
            
        Returns:
            List of sanitizer finding descriptions
        """
        findings = []
        
        # ASan patterns
        if "ERROR: AddressSanitizer" in output:
            findings.append("AddressSanitizer error detected")
        if "ERROR: LeakSanitizer" in output:
            findings.append("Memory leak detected")
            
        # TSan patterns
        if "WARNING: ThreadSanitizer: data race" in output:
            findings.append("Data race detected")
        if "WARNING: ThreadSanitizer: lock-order-inversion" in output:
            findings.append("Deadlock risk detected")
        
        return findings
