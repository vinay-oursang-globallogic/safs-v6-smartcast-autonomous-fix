"""
SAFS v6.0 — Validation Models

Tri-Path Validation data models for Stage 7.

Three validation paths:
- PATH α (QEMU): ARM cross-compile + ASan/TSan validation
- PATH β (Playwright): Headless Chromium + companion mock
- PATH γ (On-Device): Real TV validation via vizio-mcp MCP servers
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ValidationPath(str, Enum):
    """Three validation paths"""
    ALPHA_QEMU = "alpha"  # PATH α: QEMU ARM
    BETA_PLAYWRIGHT = "beta"  # PATH β: Playwright
    GAMMA_ONDEVICE = "gamma"  # PATH γ: On-Device


class ChipsetTarget(str, Enum):
    """MediaTek chipset compilation targets"""
    MTK_LEGACY = "MTK_LEGACY"  # GCC 4.9, glibc 2.14 (MT5396/MT5398)
    MTK_CURRENT = "MTK_CURRENT"  # GCC 9.3, glibc 2.31 (MT5670/MT5882)


class SanitizerType(str, Enum):
    """AddressSanitizer types for LOKi C++ validation"""
    ASAN = "address"  # AddressSanitizer (memory errors)
    TSAN = "thread"  # ThreadSanitizer (race conditions)
    UBSAN = "undefined"  # UndefinedBehaviorSanitizer


class PathValidationResult(BaseModel):
    """
    Single validation path result (α, β, or γ).
    
    Each path has:
    - passed: Overall pass/fail
    - test_results: Individual test outcomes
    - evidence: Logs, screenshots, sanitizer output
    - duration: Time taken
    """
    path: ValidationPath = Field(..., description="Validation path (alpha/beta/gamma)")
    passed: bool = Field(..., description="Overall pass/fail for this path")
    test_results: Dict[str, bool] = Field(
        default_factory=dict, 
        description="Individual test results {test_name: passed}"
    )
    evidence: Dict[str, Any] = Field(
        default_factory=dict,
        description="Evidence: logs, screenshots, sanitizer output, scene graphs"
    )
    failure_reasons: List[str] = Field(
        default_factory=list, 
        description="Reasons for test failures"
    )
    duration_seconds: float = Field(default=0.0, description="Validation duration")
    executed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Execution timestamp"
    )


class QEMUValidationResult(BaseModel):
    """
    QEMU ARM validation result (PATH α).
    
    Includes:
    - Per-chipset results (MTK_LEGACY, MTK_CURRENT)
    - AddressSanitizer/ThreadSanitizer findings
    - Unit test results
    """
    mtk_legacy_passed: Optional[bool] = Field(
        None, description="MTK_LEGACY (GCC 4.9) passed"
    )
    mtk_current_passed: Optional[bool] = Field(
        None, description="MTK_CURRENT (GCC 9.3) passed"
    )
    sanitizer_findings: List[str] = Field(
        default_factory=list,
        description="ASan/TSan findings (empty if clean)"
    )
    unit_test_output: str = Field(default="", description="pytest output")
    compilation_logs: Dict[str, str] = Field(
        default_factory=dict,
        description="Compilation logs per chipset {chipset: logs}"
    )


class PlaywrightValidationResult(BaseModel):
    """
    Playwright validation result (PATH β).
    
    Includes:
    - Scenario execution results
    - Console errors
    - Network request logs
    - Companion library mock interactions
    """
    scenarios_passed: Dict[str, bool] = Field(
        default_factory=dict,
        description="Scenario results {scenario_name: passed}"
    )
    console_errors: List[str] = Field(
        default_factory=list,
        description="JavaScript console errors"
    )
    network_logs: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Network request/response logs"
    )
    companion_mock_version: Optional[str] = Field(
        None, description="Companion library version used in mock"
    )
    screenshots: Dict[str, str] = Field(
        default_factory=dict,
        description="Screenshots {scenario: base64_data}"
    )


class OnDeviceValidationResult(BaseModel):
    """
    On-device validation result (PATH γ).
    
    Includes:
    - Before/after log comparison
    - Scene graph verification
    - Screenshots
    - Registry values
    - Firmware/companion version info
    """
    firmware_version: Optional[str] = Field(
        None, description="TV firmware version"
    )
    companion_library_version: Optional[str] = Field(
        None, description="LOKi companion library version"
    )
    baseline_logs: List[str] = Field(
        default_factory=list,
        description="Logs before fix deployment"
    )
    postfix_logs: List[str] = Field(
        default_factory=list,
        description="Logs after fix deployment"
    )
    new_errors: List[str] = Field(
        default_factory=list,
        description="New errors introduced by fix (should be empty)"
    )
    scene_graph: Optional[Dict[str, Any]] = Field(
        None, description="Scene graph from vizio-loki"
    )
    screenshots: Dict[str, str] = Field(
        default_factory=dict,
        description="Screenshots {step: base64_data}"
    )
    registry_values: Dict[str, str] = Field(
        default_factory=dict,
        description="Registry values captured {path: value}"
    )
    reproduction_successful: bool = Field(
        default=False,
        description="Whether bug was successfully reproduced before fix"
    )


class CandidateValidationResult(BaseModel):
    """
    Complete validation result for a single FixCandidate.
    
    Contains results from all required paths (α, β, γ) and overall decision.
    """
    candidate_id: str = Field(..., description="FixCandidate fix_id")
    
    # Path results
    alpha_result: Optional[PathValidationResult] = Field(
        None, description="PATH α (QEMU) result"
    )
    beta_result: Optional[PathValidationResult] = Field(
        None, description="PATH β (Playwright) result"
    )
    gamma_result: Optional[PathValidationResult] = Field(
        None, description="PATH γ (On-Device) result"
    )
    
    # Detailed results per path
    qemu_details: Optional[QEMUValidationResult] = Field(
        None, description="Detailed QEMU validation results"
    )
    playwright_details: Optional[PlaywrightValidationResult] = Field(
        None, description="Detailed Playwright validation results"
    )
    ondevice_details: Optional[OnDeviceValidationResult] = Field(
        None, description="Detailed on-device validation results"
    )
    
    # Overall result
    overall_passed: bool = Field(..., description="All required paths passed")
    required_paths: List[ValidationPath] = Field(
        default_factory=list,
        description="Which paths were required for this bug category"
    )
    confidence_boost: float = Field(
        default=0.0,
        description="Confidence boost from validation (+0.15 if on-device passed)"
    )
    validation_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Validation quality score (0.0-1.0)"
    )
    total_duration_seconds: float = Field(
        default=0.0,
        description="Total validation duration across all paths"
    )
    validated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Validation completion timestamp"
    )
