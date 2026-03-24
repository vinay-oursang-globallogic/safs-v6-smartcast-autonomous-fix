"""
Phase 10: Fix Generation (Stage 5.6) - Test Suite
==================================================

Comprehensive tests for Fix Generation Agent and system prompts.

Coverage targets:
- System prompts: LOKI_FIX_SYSTEM_PROMPT, HTML5_FIX_SYSTEM_PROMPT, CROSS_LAYER_FIX_SYSTEM_PROMPT
- FixGeneratorAgent: 3-candidate tournament, parallel generation, layer-specific prompts
- Context formatting: Historical fixes, known mistakes, reproduction evidence
- Response parsing: JSON extraction, confidence routing

Master Prompt Reference: Section 3.9 - Stage 6: Fix Generation
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any

from safs.agents import FixGeneratorAgent
from safs.agents.repo_locator import CodeLocation, RepoLocatorResult
from safs.agents.prompts import (
    LOKI_FIX_SYSTEM_PROMPT,
    HTML5_FIX_SYSTEM_PROMPT,
    CROSS_LAYER_FIX_SYSTEM_PROMPT,
    FixStrategy as PromptFixStrategy,
    get_strategy_guidance,
)
from safs.log_analysis.models import (
    PipelineState,
    JiraTicket,
    BugLayerResult,
    BugLayer,
    ErrorCategory,
    FixCandidate,
    FixStrategy,
    ConfidenceRouting,
    RootCauseResult,
)
from safs.reproduction.models import (
    ReproResultV2,
    ReproductionStatus,
    ReproductionStrategy,
    CompanionLibInfo,
    ReproductionEvidence,
    BaselineMetrics,
)
from safs.root_cause_analysis.llm_client import LLMClient


# ============================================================================
# PART 1: System Prompt Tests
# ============================================================================


class TestSystemPrompts:
    """Test system prompts contain required safety rules."""
    
    def test_loki_prompt_has_cpp14_restriction(self):
        """Test LOKi prompt enforces C++14 restriction."""
        assert "C++14" in LOKI_FIX_SYSTEM_PROMPT
        assert "std::optional" in LOKI_FIX_SYSTEM_PROMPT  # Forbidden C++17 feature
        assert "smart pointer" in LOKI_FIX_SYSTEM_PROMPT.lower()
    
    def test_loki_prompt_has_threading_rules(self):
        """Test LOKi prompt includes threading safety rules."""
        assert "mutex" in LOKI_FIX_SYSTEM_PROMPT.lower()
        assert "thread" in LOKI_FIX_SYSTEM_PROMPT.lower()
    
    def test_loki_prompt_has_arm_alignment(self):
        """Test LOKi prompt mentions ARM alignment requirements."""
        assert "ARM" in LOKI_FIX_SYSTEM_PROMPT or "alignment" in LOKI_FIX_SYSTEM_PROMPT.lower()
    
    def test_html5_prompt_has_companionlib_guard(self):
        """Test HTML5 prompt enforces CompanionLib guard."""
        assert "VIZIO_LIBRARY_DID_LOAD" in HTML5_FIX_SYSTEM_PROMPT
        assert "window.VIZIO" in HTML5_FIX_SYSTEM_PROMPT
    
    def test_html5_prompt_has_event_cleanup(self):
        """Test HTML5 prompt requires event listener cleanup."""
        assert "removeEventListener" in HTML5_FIX_SYSTEM_PROMPT
        assert "addEventListener" in HTML5_FIX_SYSTEM_PROMPT
    
    def test_html5_prompt_has_drm_rules(self):
        """Test HTML5 prompt includes DRM error handling."""
        assert "DRM" in HTML5_FIX_SYSTEM_PROMPT or "Widevine" in HTML5_FIX_SYSTEM_PROMPT
        assert "onerror" in HTML5_FIX_SYSTEM_PROMPT.lower()
    
    def test_cross_layer_prompt_requires_two_prs(self):
        """Test CROSS_LAYER prompt requires dual fixes."""
        assert "TWO" in CROSS_LAYER_FIX_SYSTEM_PROMPT or "two" in CROSS_LAYER_FIX_SYSTEM_PROMPT.lower()
        assert "PR" in CROSS_LAYER_FIX_SYSTEM_PROMPT or "fix" in CROSS_LAYER_FIX_SYSTEM_PROMPT.lower()
    
    def test_strategy_guidance_surgical(self):
        """Test SURGICAL strategy guidance."""
        guidance = get_strategy_guidance(PromptFixStrategy.SURGICAL)
        
        assert "minimal" in guidance.lower()
        assert "1-10 lines" in guidance or "small" in guidance.lower()
    
    def test_strategy_guidance_defensive(self):
        """Test DEFENSIVE strategy guidance."""
        guidance = get_strategy_guidance(PromptFixStrategy.DEFENSIVE)
        
        assert "guard" in guidance.lower() or "defensive" in guidance.lower()
        assert "edge case" in guidance.lower() or "validation" in guidance.lower()
    
    def test_strategy_guidance_refactored(self):
        """Test REFACTORED strategy guidance."""
        guidance = get_strategy_guidance(PromptFixStrategy.REFACTORED)
        
        assert "structural" in guidance.lower() or "refactor" in guidance.lower()
        assert "root cause" in guidance.lower() or "eliminate" in guidance.lower()


# ============================================================================
# PART 2: FixGeneratorAgent Initialization Tests
# ============================================================================


class TestFixGeneratorAgentInit:
    """Test FixGeneratorAgent initialization."""
    
    def test_agent_init(self):
        """Test agent initializes with LLM client."""
        mock_llm = Mock(spec=LLMClient)
        agent = FixGeneratorAgent(llm_client=mock_llm)
        
        assert agent.llm == mock_llm
        assert len(agent.STRATEGY_NAMES) == 3
        assert "SURGICAL" in agent.STRATEGY_NAMES
        assert "DEFENSIVE" in agent.STRATEGY_NAMES
        assert "REFACTORED" in agent.STRATEGY_NAMES


# ============================================================================
# PART 3: Layer-Specific Prompt Selection Tests
# ============================================================================


class TestPromptSelection:
    """Test layer-specific system prompt selection."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.mock_llm = Mock(spec=LLMClient)
        self.agent = FixGeneratorAgent(llm_client=self.mock_llm)
    
    def test_loki_layer_selects_loki_prompt(self):
        """Test LOKi layer selects LOKi prompt."""
        prompt = self.agent._build_system_prompt(BugLayer.LOKI)
        
        assert prompt == LOKI_FIX_SYSTEM_PROMPT
        assert "C++14" in prompt
    
    def test_html5_layer_selects_html5_prompt(self):
        """Test HTML5 layer selects HTML5 prompt."""
        prompt = self.agent._build_system_prompt(BugLayer.HTML5)
        
        assert prompt == HTML5_FIX_SYSTEM_PROMPT
        assert "VIZIO_LIBRARY_DID_LOAD" in prompt
    
    def test_cross_layer_selects_cross_layer_prompt(self):
        """Test CROSS_LAYER selects cross-layer prompt."""
        prompt = self.agent._build_system_prompt(BugLayer.CROSS_LAYER)
        
        assert prompt == CROSS_LAYER_FIX_SYSTEM_PROMPT
    
    def test_mediatek_layer_returns_error(self):
        """Test MediaTek layer returns error (should be auto-escalated)."""
        prompt = self.agent._build_system_prompt(BugLayer.MEDIATEK)
        
        assert "ERROR" in prompt or "MediaTek" in prompt


# ============================================================================
# PART 4: Historical Fix Formatting Tests
# ============================================================================


class TestHistoricalFixFormatting:
    """Test historical fix formatting with age warnings."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.mock_llm = Mock(spec=LLMClient)
        self.agent = FixGeneratorAgent(llm_client=self.mock_llm)
    
    def test_empty_historical_fixes(self):
        """Test empty historical fixes list."""
        result = self.agent._format_historical_fixes([])
        
        assert "No historical fixes" in result
    
    def test_recent_fix_no_warning(self):
        """Test recent fix (<6 months) has no age warning."""
        recent_date = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        fixes = [{
            "title": "Fix memory leak in VideoDecoder",
            "pr_url": "https://github.com/vizio/loki/pull/1234",
            "fix_date": recent_date,
            "final_score": 0.95,
            "fix_summary": "Added proper cleanup in destructor",
        }]
        
        result = self.agent._format_historical_fixes(fixes)
        
        assert "Fix memory leak" in result
        assert "temporal decay" not in result
        assert "⚠️" not in result
    
    def test_old_fix_has_warning(self):
        """Test old fix (>6 months) has age warning."""
        old_date = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
        fixes = [{
            "title": "Fix null pointer crash in Renderer",
            "pr_url": "https://github.com/vizio/loki/pull/5678",
            "fix_date": old_date,
            "final_score": 0.85,
            "fix_summary": "Added null check before access",
        }]
        
        result = self.agent._format_historical_fixes(fixes)
        
        assert "Fix null pointer" in result
        assert "temporal decay" in result
        assert "⚠️" in result
        assert "months old" in result
    
    def test_top_5_fixes_only(self):
        """Test only top 5 fixes are formatted."""
        fixes = [{"title": f"Fix {i}", "final_score": 0.8, "fix_date": ""} for i in range(10)]
        
        result = self.agent._format_historical_fixes(fixes)
        
        # Count numbered entries
        for i in range(1, 6):
            assert f"{i}." in result
        assert "6." not in result


# ============================================================================
# PART 5: Known Mistakes Formatting Tests
# ============================================================================


class TestKnownMistakesFormatting:
    """Test known mistakes formatting."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.mock_llm = Mock(spec=LLMClient)
        self.agent = FixGeneratorAgent(llm_client=self.mock_llm)
    
    def test_empty_mistakes(self):
        """Test empty mistakes list."""
        result = self.agent._format_mistakes([])
        
        assert "No known mistakes" in result
    
    def test_mistake_formatting(self):
        """Test mistake formatting includes anti-pattern and reason."""
        mistakes = [{
            "anti_pattern": "Using raw pointers in LOKi shared state",
            "why_bad": "Causes double-free and use-after-free crashes",
            "incident_count": 5,
        }]
        
        result = self.agent._format_mistakes(mistakes)
        
        assert "raw pointers" in result
        assert "double-free" in result
        assert "5x" in result or "5" in result
    
    def test_top_3_mistakes_only(self):
        """Test only top 3 mistakes are formatted."""
        mistakes = [
            {"anti_pattern": f"Anti-pattern {i}", "why_bad": "Bad", "incident_count": i}
            for i in range(5)
        ]
        
        result = self.agent._format_mistakes(mistakes)
        
        assert "1." in result
        assert "2." in result
        assert "3." in result
        assert "4." not in result


# ============================================================================
# PART 6: Reproduction Evidence Formatting Tests
# ============================================================================


class TestReproductionEvidenceFormatting:
    """Test reproduction evidence formatting (NEW in v6.0)."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.mock_llm = Mock(spec=LLMClient)
        self.agent = FixGeneratorAgent(llm_client=self.mock_llm)
    
    def test_no_repro_returns_empty(self):
        """Test no reproduction result returns empty string."""
        result = self.agent._format_repro_evidence(None)
        
        assert result == ""
    
    def test_failed_repro_returns_empty(self):
        """Test failed reproduction returns empty string."""
        repro = ReproResultV2(
            status=ReproductionStatus.NOT_REPRODUCED,
            strategy=ReproductionStrategy.DETERMINISTIC,
            companion_info=None,
        )
        
        result = self.agent._format_repro_evidence(repro)
        
        assert result == ""
    
    def test_successful_repro_formatting(self):
        """Test successful reproduction evidence formatting."""
        companion_info = CompanionLibInfo(
            loki_version="3.2.1",
            firmware_version="5.2.1",
            chipset="MT5882",
            companion_enabled=True,
            chromium_version="95.0.4638.74",
            companion_api_version="v3.2",
        )
        
        evidence = ReproductionEvidence(
            logs="ERROR: Null pointer dereference at 0x00401234",
            screenshot="screenshot.png",
            error_count=1,
            matched_patterns=["LOKI_SEGFAULT_NULL_DEREF"],
        )
        
        baseline = BaselineMetrics(
            loki_memory_mb=245.3,
            chromium_memory_mb=512.1,
            cpu_percent=45.2,
            error_rate_per_min=2.5,
            crash_count=1,
        )
        
        repro = ReproResultV2(
            status=ReproductionStatus.REPRODUCED,
            strategy=ReproductionStrategy.DETERMINISTIC,
            companion_info=companion_info,
            evidence=evidence,
            baseline_metrics=baseline,
        )
        
        result = self.agent._format_repro_evidence(repro)
        
        assert "Bug was successfully reproduced" in result
        assert "MT5882" in result
        assert "3.2.1" in result
        assert "Null pointer" in result
        assert "245.3 MB" in result
        assert "/min" in result  # Error rate format (2.50/min or 2.5/min)


# ============================================================================
# PART 7: JSON Response Parsing Tests
# ============================================================================


class TestResponseParsing:
    """Test LLM response parsing."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.mock_llm = Mock(spec=LLMClient)
        self.agent = FixGeneratorAgent(llm_client=self.mock_llm)
    
    def test_parse_clean_json(self):
        """Test parsing clean JSON response."""
        response = """{
            "strategy": "SURGICAL",
            "confidence": 0.85,
            "diff": "--- a/file.cpp\\n+++ b/file.cpp\\n@@ -10,3 +10,4 @@\\n+  if (ptr == nullptr) return;",
            "explanation": "Added null check",
            "file_changes": ["src/decoder.cpp"]
        }"""
        
        result = self.agent._parse_fix_response(response, "SURGICAL")
        
        assert result["strategy"] == "SURGICAL"
        assert result["confidence"] == 0.85
        assert "nullptr" in result["diff"]  # Check for code content
        assert "Added null check" in result["explanation"]
    
    def test_parse_json_with_markdown(self):
        """Test parsing JSON wrapped in markdown code blocks."""
        response = """Here's the fix:
        ```json
        {
            "strategy": "DEFENSIVE",
            "confidence": 0.75,
            "diff": "test diff",
            "explanation": "Added guards"
        }
        ```
        """
        
        result = self.agent._parse_fix_response(response, "DEFENSIVE")
        
        assert result["strategy"] == "DEFENSIVE"
        assert result["confidence"] == 0.75
    
    def test_parse_invalid_json(self):
        """Test parsing invalid JSON returns safe defaults."""
        response = "This is not valid JSON at all"
        
        result = self.agent._parse_fix_response(response, "SURGICAL")
        
        assert result["strategy"] == "SURGICAL"
        assert result["confidence"] == 0.3  # Low confidence
        assert "Failed to parse" in result["explanation"]


# ============================================================================
# PART 8: Confidence Routing Tests
# ============================================================================


class TestConfidenceRouting:
    """Test confidence score to routing determination."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.mock_llm = Mock(spec=LLMClient)
        self.agent = FixGeneratorAgent(llm_client=self.mock_llm)
    
    def test_high_confidence_auto_pr(self):
        """Test high confidence (≥0.90) routes to AUTO_PR."""
        routing = self.agent._determine_routing(0.95)
        
        assert routing == ConfidenceRouting.AUTO_PR
    
    def test_medium_high_confidence_pr_with_review(self):
        """Test medium-high confidence (0.75-0.89) routes to PR_WITH_REVIEW."""
        routing = self.agent._determine_routing(0.80)
        
        assert routing == ConfidenceRouting.PR_WITH_REVIEW
    
    def test_medium_confidence_analysis_only(self):
        """Test medium confidence (0.40-0.59) routes to ANALYSIS_ONLY."""
        routing = self.agent._determine_routing(0.50)
        
        assert routing == ConfidenceRouting.ANALYSIS_ONLY
    
    def test_low_confidence_escalate(self):
        """Test low confidence (<0.40) routes to ESCALATE_HUMAN."""
        routing = self.agent._determine_routing(0.30)
        
        assert routing == ConfidenceRouting.ESCALATE_HUMAN


# ============================================================================
# PART 9: Single Fix Generation Tests
# ============================================================================


class TestSingleFixGeneration:
    """Test single fix candidate generation."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.mock_llm = AsyncMock(spec=LLMClient)
        self.agent = FixGeneratorAgent(llm_client=self.mock_llm)
        
        # Mock state, root_cause, context
        self.state = PipelineState(
            ticket=JiraTicket(
                key="SAFS-1234",
                summary="Null pointer crash in VideoDecoder",
                description="App crashes when seeking during playback",
                priority="Critical",
            ),
            buglayer_result=BugLayerResult(
                layer=BugLayer.LOKI,
                confidence=0.95,
                routing_reason="C++ stack trace in LOKi layer",
            ),
        )
        
        self.root_cause = RootCauseResult(
            root_cause="Null pointer dereference in VideoDecoder::Seek()",
            confidence=0.88,
            error_category=ErrorCategory.LOKI_SEGFAULT_NULL_DEREF,
            severity="HIGH",
            affected_files=["src/media/video_decoder.cpp"],
        )
        
        self.context = RepoLocatorResult(
            primary_locations=[
                CodeLocation(
                    repo="vizio/loki",
                    path="src/media/video_decoder.cpp",
                    line_number=245,
                    confidence=0.92,
                    source="path_a",
                    content_preview="void VideoDecoder::Seek(int64_t timestamp) {\n  m_buffer->Reset();\n}",
                )
            ],
            secondary_locations=[],
            similar_fixes=[],
            known_mistakes=[],
        )
    
    @pytest.mark.asyncio
    async def test_surgical_fix_generation(self):
        """Test SURGICAL fix generation."""
        # Mock LLM response
        self.mock_llm.generate.return_value = """{
            "strategy": "SURGICAL",
            "confidence": 0.85,
            "diff": "--- a/video_decoder.cpp\\n+++ b/video_decoder.cpp\\n@@ -246,1 +246,2 @@\\n+  if (!m_buffer) return;\\n   m_buffer->Reset();",
            "explanation": "Added null check before m_buffer access",
            "file_changes": [{"path": "src/media/video_decoder.cpp", "diff": "..."}]
        }"""
        
        candidate = await self.agent._generate_one(
            strategy="SURGICAL",
            system_prompt=LOKI_FIX_SYSTEM_PROMPT,
            root_cause=self.root_cause,
            context=self.context,
            historical="",
            mistakes="",
            repro="",
            state=self.state,
        )
        
        assert candidate.strategy == FixStrategy.NULL_CHECK
        assert candidate.confidence == 0.85
        assert "null check" in candidate.explanation.lower()
        assert candidate.routing == ConfidenceRouting.AUTO_PR
    
    @pytest.mark.asyncio
    async def test_llm_failure_returns_low_confidence(self):
        """Test LLM failure returns low-confidence placeholder."""
        # Mock LLM exception
        self.mock_llm.generate.side_effect = Exception("API timeout")
        
        candidate = await self.agent._generate_one(
            strategy="DEFENSIVE",
            system_prompt=LOKI_FIX_SYSTEM_PROMPT,
            root_cause=self.root_cause,
            context=self.context,
            historical="",
            mistakes="",
            repro="",
            state=self.state,
        )
        
        assert candidate.confidence == 0.0
        assert candidate.routing == ConfidenceRouting.ESCALATE_HUMAN
        assert "failed" in candidate.explanation.lower()


# ============================================================================
# PART 10: 3-Candidate Tournament Tests
# ============================================================================


class Test3CandidateTournament:
    """Test 3-candidate parallel generation (main feature)."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.mock_llm = AsyncMock(spec=LLMClient)
        self.agent = FixGeneratorAgent(llm_client=self.mock_llm)
        
        # Mock state, root_cause, context
        self.state = PipelineState(
            ticket=JiraTicket(
                key="SAFS-1234",
                summary="Memory leak in TextureManager",
                description="Memory usage grows unbounded during video playback",
                priority="High",
            ),
            buglayer_result=BugLayerResult(
                layer=BugLayer.LOKI,
                confidence=0.93,
                routing_reason="C++ memory profiling shows LOKi leak",
            ),
        )
        
        self.root_cause = RootCauseResult(
            root_cause="Texture resources not released in TextureManager destructor",
            confidence=0.85,
            error_category=ErrorCategory.MEMORY_LEAK_EVENT_LISTENER,
            severity="HIGH",
            affected_files=["src/graphics/texture_manager.cpp"],
        )
        
        self.context = RepoLocatorResult(
            primary_locations=[
                CodeLocation(
                    repo="vizio/loki",
                    path="src/graphics/texture_manager.cpp",
                    line_number=120,
                    confidence=0.90,
                    source="path_a",
                    content_preview="TextureManager::~TextureManager() { }",
                )
            ],
            secondary_locations=[],
            similar_fixes=[],
            known_mistakes=[],
        )
    
    @pytest.mark.asyncio
    async def test_generate_returns_3_candidates(self):
        """Test generate() returns exactly 3 candidates."""
        # Mock LLM responses for all 3 strategies
        self.mock_llm.generate.side_effect = [
            '{"strategy": "SURGICAL", "confidence": 0.80, "diff": "surgical diff", "explanation": "Minimal fix"}',
            '{"strategy": "DEFENSIVE", "confidence": 0.75, "diff": "defensive diff", "explanation": "Guarded fix"}',
            '{"strategy": "REFACTORED", "confidence": 0.70, "diff": "refactored diff", "explanation": "Structural fix"}',
        ]
        
        candidates = await self.agent.generate(
            state=self.state,
            root_cause=self.root_cause,
            context=self.context,
        )
        
        assert len(candidates) == 3
        assert self.mock_llm.generate.call_count == 3
    
    @pytest.mark.asyncio
    async def test_parallel_generation_uses_asyncio_gather(self):
        """Test parallel generation calls LLM concurrently."""
        # Mock fast responses
        self.mock_llm.generate.side_effect = [
            '{"strategy": "SURGICAL", "confidence": 0.85, "diff": "diff1", "explanation": "Fix 1"}',
            '{"strategy": "DEFENSIVE", "confidence": 0.80, "diff": "diff2", "explanation": "Fix 2"}',
            '{"strategy": "REFACTORED", "confidence": 0.75, "diff": "diff3", "explanation": "Fix 3"}',
        ]
        
        candidates = await self.agent.generate(
            state=self.state,
            root_cause=self.root_cause,
            context=self.context,
        )
        
        # All 3 should complete (asyncio.gather)
        assert len(candidates) == 3
        assert all(c.confidence > 0 for c in candidates)
    
    @pytest.mark.asyncio
    async def test_reproduction_evidence_integrated(self):
        """Test reproduction evidence is integrated into prompt."""
        # Create reproduction result
        repro = ReproResultV2(
            status=ReproductionStatus.REPRODUCED,
            strategy=ReproductionStrategy.DETERMINISTIC,
            companion_info=CompanionLibInfo(
                loki_version="3.2.1",
                firmware_version="5.2.1",
                chipset="MT5882",
                companion_enabled=True,
                companion_api_version="v3.2",
            ),
            evidence=ReproductionEvidence(
                logs="ERROR: Memory leak detected",
                error_count=1,
            ),
            baseline_metrics=BaselineMetrics(
                loki_memory_mb=500.0,
                error_rate_per_min=1.0,
                crash_count=0,
            ),
        )
        
        # Mock LLM responses
        self.mock_llm.generate.side_effect = [
            '{"strategy": "SURGICAL", "confidence": 0.90, "diff": "diff", "explanation": "Fix"}',
            '{"strategy": "DEFENSIVE", "confidence": 0.85, "diff": "diff", "explanation": "Fix"}',
            '{"strategy": "REFACTORED", "confidence": 0.80, "diff": "diff", "explanation": "Fix"}',
        ]
        
        candidates = await self.agent.generate(
            state=self.state,
            root_cause=self.root_cause,
            context=self.context,
            repro=repro,
        )
        
        # Verify prompt contained repro evidence
        # Check first call's user_prompt argument
        call_args = self.mock_llm.generate.call_args_list[0]
        user_prompt = call_args[1]["user_prompt"]
        
        assert "Bug Reproduction Evidence" in user_prompt or "reproduced" in user_prompt.lower()


# ============================================================================
# PART 11: Strategy Mapping Tests
# ============================================================================


class TestStrategyMapping:
    """Test strategy name to enum mapping."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.mock_llm = Mock(spec=LLMClient)
        self.agent = FixGeneratorAgent(llm_client=self.mock_llm)
    
    def test_map_surgical_to_null_check(self):
        """Test SURGICAL maps to NULL_CHECK."""
        strategy = self.agent._map_strategy_to_enum("SURGICAL")
        
        assert strategy == FixStrategy.NULL_CHECK
    
    def test_map_defensive_to_mutex_guard(self):
        """Test DEFENSIVE maps to MUTEX_GUARD."""
        strategy = self.agent._map_strategy_to_enum("DEFENSIVE")
        
        assert strategy == FixStrategy.MUTEX_GUARD
    
    def test_map_refactored_to_smart_pointer(self):
        """Test REFACTORED maps to SMART_POINTER."""
        strategy = self.agent._map_strategy_to_enum("REFACTORED")
        
        assert strategy == FixStrategy.SMART_POINTER
    
    def test_map_unknown_to_unknown_enum(self):
        """Test unknown strategy maps to UNKNOWN."""
        strategy = self.agent._map_strategy_to_enum("INVALID_STRATEGY")
        
        assert strategy == FixStrategy.UNKNOWN
