"""
Reproduction Module
===================

Stage 5.5: Bug Reproduction (NEW in v6.0)

Attempts to reproduce the bug on a dev TV before fix generation.
Powered by vizio-mcp MCP servers (vizio-remote, vizio-ssh, vizio-loki).

Benefits:
1. Prevents wasted effort on non-reproducible issues
2. Captures baseline evidence for before/after comparison
3. Establishes ground-truth metrics for validation
4. Increases confidence when bug IS reproduced

Components:
- agent.py: BugReproductionAgent - Main reproduction orchestrator
- device_resolver.py: DynamicCompanionLibResolver - Live registry resolution
- models.py: CompanionLibInfo, ReproductionStrategy, ReproductionEvidence
"""

from .agent import BugReproductionAgent
from .device_resolver import DynamicCompanionLibResolver
from .models import (
    CompanionLibInfo,
    ReproductionStrategy,
    ReproductionStatus,
    ReproductionEvidence,
    BaselineMetrics,
    ReproStep,
    ReproResultV2,
)

__all__ = [
    "BugReproductionAgent",
    "DynamicCompanionLibResolver",
    "CompanionLibInfo",
    "ReproductionStrategy",
    "ReproductionStatus",
    "ReproductionEvidence",
    "BaselineMetrics",
    "ReproStep",
    "ReproResultV2",
]
