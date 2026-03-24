"""
Agents Module
=============

LangGraph orchestration, fix generation, PR creation, confidence ensemble.

Extended from mcp-second-screen/jira_auto_fixer POC.

Components:
- orchestrator.py: LangGraph orchestrator with BugLayer routing
- root_cause.py: Stage 3 (heuristic pre-filter + LLM synthesis)
- repo_locator.py: Stage 4 (uses RetrievalRouter for file location)
- fix_generator.py: Stage 6 (3-candidate tournament: SURGICAL/DEFENSIVE/REFACTORED)
- pr_creator.py: Stage 8 (DRAFT PRs only, ported from github_client.py)
- confidence_ensemble.py: Stage 7.5 (4-signal + on-device boost)
- regression_test_gen.py: Async test generation (ported from integration_test_generator.py)
- self_healing.py: Developer correction → Qdrant mistake store
"""

# Import individual components (not orchestrator to avoid circular imports)
from .repo_locator import RepoLocatorAgent, RepoLocatorResult, CodeLocation
from .fix_generator import FixGeneratorAgent
from .pr_creator import PRCreatorAgent, PRResult
from .confidence_ensemble import (
    ConfidenceEnsemble,
    ConfidenceSignals,
    ConfidenceResult,
    build_confidence_signals,
)
from .self_healing import SelfHealingAgent

# Orchestrator is imported separately to avoid circular imports
# from .orchestrator import SAFSOrchestrator

__all__ = [
    # "SAFSOrchestrator",  # Import directly: from safs.agents.orchestrator import SAFSOrchestrator
    "RepoLocatorAgent",
    "RepoLocatorResult",
    "CodeLocation",
    "FixGeneratorAgent",
    "PRCreatorAgent",
    "PRResult",
    "ConfidenceEnsemble",
    "ConfidenceSignals",
    "ConfidenceResult",
    "build_confidence_signals",
    "SelfHealingAgent",
]
