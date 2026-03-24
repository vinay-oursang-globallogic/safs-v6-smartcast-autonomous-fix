"""
Validation Module
=================

Tri-path validation: QEMU (α) + Playwright (β) + On-Device (γ)

Components:
- models.py: Validation data models
- qemu_validator.py: PATH α (QEMU ARM cross-compile + ASan/TSan)
- playwright_validator.py: PATH β (Headless Chromium + companion mock)
- on_device_validator.py: PATH γ (Real TV via vizio-mcp)
- tri_path_validator.py: Orchestrates α + β + γ per bug category
"""

from .models import (
    CandidateValidationResult,
    ChipsetTarget,
    OnDeviceValidationResult,
    PathValidationResult,
    PlaywrightValidationResult,
    QEMUValidationResult,
    SanitizerType,
    ValidationPath,
)
from .on_device_validator import OnDeviceValidator
from .playwright_validator import PlaywrightValidator
from .qemu_validator import QEMUValidator
from .tri_path_validator import TriPathValidator
from .multi_chipset_validator import MultiChipsetValidator
from .companion_mock import CompanionLibMockServer
from .drm_tester import DRMTester, DRMTestResult

__all__ = [
    # Models
    "ValidationPath",
    "ChipsetTarget",
    "SanitizerType",
    "PathValidationResult",
    "QEMUValidationResult",
    "PlaywrightValidationResult",
    "OnDeviceValidationResult",
    "CandidateValidationResult",
    # Validators
    "QEMUValidator",
    "PlaywrightValidator",
    "OnDeviceValidator",
    "TriPathValidator",
    "MultiChipsetValidator",
    "CompanionLibMockServer",
    "DRMTester",
    "DRMTestResult",
]

