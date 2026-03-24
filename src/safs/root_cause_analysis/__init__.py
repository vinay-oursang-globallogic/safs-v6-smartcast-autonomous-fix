"""
SAFS v6.0 — Root Cause Analysis Module

Stage 3 of the SAFS pipeline: Two-phase root cause analysis.

Phase 1: Heuristic pre-filter (from POC SmartTVErrorAnalyzer)
Phase 2: LLM synthesis (Claude Haiku cross-references all evidence)

Components:
- RootCauseAgent: Main orchestrator for RCA
- LLMClient: Async Anthropic Claude client with structured output
- Prompts: Layer-specific system prompts (LOKi, HTML5, MediaTek, Cross-Layer, Unknown)

Usage:
    from safs.root_cause_analysis import RootCauseAgent
    
    agent = RootCauseAgent(api_key=os.getenv("ANTHROPIC_API_KEY"))
    result = await agent.analyze(
        state=pipeline_state,
        log_analysis=log_intelligence_result,
    )
    print(f"Root cause: {result.root_cause}")
    print(f"Confidence: {result.confidence:.2f}")
    print(f"Category: {result.error_category.value}")
"""

from safs.root_cause_analysis.agent import RootCauseAgent
from safs.root_cause_analysis.llm_client import (
    LLMClient,
    LLMError,
    LLMRateLimitError,
    LLMValidationError,
)
from safs.root_cause_analysis.prompts import (
    get_system_prompt,
    LOKI_RCA_SYSTEM_PROMPT,
    HTML5_RCA_SYSTEM_PROMPT,
    MEDIATEK_RCA_SYSTEM_PROMPT,
    CROSS_LAYER_RCA_SYSTEM_PROMPT,
    UNKNOWN_LAYER_RCA_SYSTEM_PROMPT,
    PromptRole,
)

__all__ = [
    "RootCauseAgent",
    "LLMClient",
    "LLMError",
    "LLMRateLimitError",
    "LLMValidationError",
    "get_system_prompt",
    "LOKI_RCA_SYSTEM_PROMPT",
    "HTML5_RCA_SYSTEM_PROMPT",
    "MEDIATEK_RCA_SYSTEM_PROMPT",
    "CROSS_LAYER_RCA_SYSTEM_PROMPT",
    "UNKNOWN_LAYER_RCA_SYSTEM_PROMPT",
    "PromptRole",
]
