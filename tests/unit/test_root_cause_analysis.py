"""
SAFS v6.0 — Root Cause Analysis Tests

Comprehensive test suite for Phase 6: Root Cause Analysis.

Test Coverage:
- Prompt selection (layer-specific prompts)
- LLM client (API calls, retries, structured output)
- RootCauseAgent (evidence formatting, LLM integration, all bug layers)

Test Structure:
- TestPrompts: Prompt selection based on bug layer
- TestLLMClient: LLM API mocking, error handling, structured output validation
- TestRootCauseAgent: Evidence formatting, layer-specific analysis, integration tests
"""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import httpx
import pytest

from src.safs.log_analysis.models import (
    BugLayer,
    ErrorCategory,
    MistakeSeverity,
    PipelineState,
    RootCauseResult,
    JiraTicket,
)
from src.safs.log_intelligence.models import (
    LogAnalysisResult,
    DrainResult,
    LogTemplate,
    ErrorCorrelation,
    Incident,
    Anomaly,
    CascadingFailure,
    LokiSymbolicationResult,
    SymbolicatedFrame,
    BacktraceFrame,
    CDPParseResult,
    CDPEvent,
    CDPException,
    SourceMappedFrame,
    SourceMapPosition,
    MediaTekKernelResult,
    KernelOops,
)
from src.safs.root_cause_analysis import (
    RootCauseAgent,
    LLMClient,
    LLMError,
    LLMRateLimitError,
    LLMValidationError,
    get_system_prompt,
    LOKI_RCA_SYSTEM_PROMPT,
    HTML5_RCA_SYSTEM_PROMPT,
    MEDIATEK_RCA_SYSTEM_PROMPT,
    CROSS_LAYER_RCA_SYSTEM_PROMPT,
    UNKNOWN_LAYER_RCA_SYSTEM_PROMPT,
)


# ============================================================================
# TEST PROMPTS
# ============================================================================


class TestPrompts:
    """Test prompt selection and layer-specific prompts."""
    
    def test_get_system_prompt_loki(self):
        """Test LOKi prompt selection."""
        prompt = get_system_prompt("LOKI")
        assert prompt == LOKI_RCA_SYSTEM_PROMPT
        assert "LOKi Native" in prompt
        assert "C++" in prompt
        assert "NULL Pointer Dereference" in prompt
    
    def test_get_system_prompt_html5(self):
        """Test HTML5 prompt selection."""
        prompt = get_system_prompt("HTML5")
        assert prompt == HTML5_RCA_SYSTEM_PROMPT
        assert "Chromium" in prompt
        assert "JavaScript" in prompt
        assert "CompanionLib" in prompt
    
    def test_get_system_prompt_mediatek(self):
        """Test MediaTek prompt selection."""
        prompt = get_system_prompt("MEDIATEK")
        assert prompt == MEDIATEK_RCA_SYSTEM_PROMPT
        assert "MediaTek" in prompt
        assert "kernel" in prompt
        assert "Auto-Escalation" in prompt
    
    def test_get_system_prompt_cross_layer(self):
        """Test Cross-Layer prompt selection."""
        prompt = get_system_prompt("CROSS_LAYER")
        assert prompt == CROSS_LAYER_RCA_SYSTEM_PROMPT
        assert "LOKi" in prompt and "HTML5" in prompt
        assert "Mojo" in prompt
    
    def test_get_system_prompt_unknown(self):
        """Test Unknown layer fallback prompt."""
        prompt = get_system_prompt("UNKNOWN")
        assert prompt == UNKNOWN_LAYER_RCA_SYSTEM_PROMPT
        assert "insufficient layer classification" in prompt
    
    def test_get_system_prompt_invalid_fallback(self):
        """Test fallback to Unknown prompt for invalid layer."""
        prompt = get_system_prompt("INVALID_LAYER")
        assert prompt == UNKNOWN_LAYER_RCA_SYSTEM_PROMPT
    
    def test_loki_prompt_contains_error_categories(self):
        """Test LOKi prompt includes LOKi error categories."""
        prompt = LOKI_RCA_SYSTEM_PROMPT
        assert "LOKI_SEGFAULT_NULL_DEREF" in prompt
        # Prompt uses descriptive names not enum names
        assert "Memory Corruption" in prompt
        assert "Race Condition" in prompt
    
    def test_html5_prompt_contains_error_categories(self):
        """Test HTML5 prompt includes HTML5 error categories."""
        prompt = HTML5_RCA_SYSTEM_PROMPT
        assert "COMPANION_LIB_TIMING" in prompt
        # Prompt uses descriptive names not enum names
        assert "EME DRM Failure" in prompt
        assert "Netflix MSL Timeout" in prompt
    
    def test_mediatek_prompt_contains_subsystems(self):
        """Test MediaTek prompt includes subsystem information."""
        prompt = MEDIATEK_RCA_SYSTEM_PROMPT
        assert "VDEC" in prompt
        assert "MALI GPU" in prompt
        assert "TrustZone" in prompt
        assert "HDCP" in prompt
    
    def test_prompts_contain_confidence_calibration(self):
        """Test all prompts contain confidence calibration guidelines."""
        for prompt in [
            LOKI_RCA_SYSTEM_PROMPT,
            HTML5_RCA_SYSTEM_PROMPT,
            MEDIATEK_RCA_SYSTEM_PROMPT,
            CROSS_LAYER_RCA_SYSTEM_PROMPT,
            UNKNOWN_LAYER_RCA_SYSTEM_PROMPT,
        ]:
            assert "Confidence Calibration" in prompt or "confidence" in prompt.lower()
            assert "0.0" in prompt or "1.0" in prompt


# ============================================================================
# TEST LLM CLIENT
# ============================================================================


class TestLLMClient:
    """Test LLM client with mocked API responses."""
    
    @pytest.fixture
    def mock_httpx_client(self):
        """Mock httpx.AsyncClient for testing."""
        client = AsyncMock(spec=httpx.AsyncClient)
        return client
    
    @pytest.fixture
    def llm_client(self, mock_httpx_client):
        """Create LLM client with mocked httpx client."""
        with patch("src.safs.root_cause_analysis.llm_client.httpx.AsyncClient", return_value=mock_httpx_client):
            client = LLMClient(api_key="test-api-key")
            client.client = mock_httpx_client
            return client
    
    @pytest.mark.asyncio
    async def test_llm_client_initialization(self):
        """Test LLM client initialization with API key."""
        with patch("src.safs.root_cause_analysis.llm_client.httpx.AsyncClient"):
            client = LLMClient(api_key="test-api-key")
            assert client.api_key == "test-api-key"
            assert client.timeout == 120  # Default timeout
            assert client.total_input_tokens == 0
            assert client.total_output_tokens == 0
    
    @pytest.mark.asyncio
    async def test_llm_client_successful_completion(self, llm_client, mock_httpx_client):
        """Test successful LLM completion with structured output."""
        # Mock successful API response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "content": [
                {
                    "text": json.dumps({
                        "root_cause": "NULL pointer dereference in AppLauncher::Launch()",
                        "confidence": 0.92,
                        "error_category": "LOKI_SEGFAULT_NULL_DEREF",
                        "severity": "CRITICAL",
                        "affected_files": ["AppLauncher.cpp"],
                    })
                }
            ],
            "usage": {
                "input_tokens": 1500,
                "output_tokens": 300,
            },
        }
        mock_httpx_client.post.return_value = mock_response
        
        # Call complete()
        result = await llm_client.complete(
            system_prompt="You are a debugger.",
            user_prompt="Analyze this crash.",
            response_model=RootCauseResult,
        )
        
        # Verify result
        assert isinstance(result, RootCauseResult)
        assert result.confidence == 0.92
        assert result.error_category == ErrorCategory.LOKI_SEGFAULT_NULL_DEREF
        assert result.severity == MistakeSeverity.CRITICAL
        
        # Verify usage tracking
        assert llm_client.total_input_tokens == 1500
        assert llm_client.total_output_tokens == 300
    
    @pytest.mark.asyncio
    async def test_llm_client_json_in_markdown_code_block(self, llm_client, mock_httpx_client):
        """Test JSON extraction from markdown code block."""
        # Mock response with JSON in markdown
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "content": [
                {
                    "text": """Here's the analysis:
```json
{
  "root_cause": "Test root cause",
  "confidence": 0.75,
  "error_category": "COMPANION_LIB_TIMING",
  "severity": "HIGH",
  "affected_files": ["VideoPlayer.js"]
}
```"""
                }
            ],
            "usage": {"input_tokens": 100, "output_tokens": 50},
        }
        mock_httpx_client.post.return_value = mock_response
        
        result = await llm_client.complete(
            system_prompt="System",
            user_prompt="User",
            response_model=RootCauseResult,
        )
        
        assert result.confidence == 0.75
        assert result.error_category == ErrorCategory.COMPANION_LIB_TIMING
    
    @pytest.mark.asyncio
    async def test_llm_client_rate_limit_retry(self, llm_client, mock_httpx_client):
        """Test retry logic on rate limit (429)."""
        # First call: 429 rate limit
        rate_limit_response = Mock()
        rate_limit_response.status_code = 429
        rate_limit_response.headers = {"retry-after": "1"}
        
        # Second call: success
        success_response = Mock()
        success_response.status_code = 200
        success_response.json.return_value = {
            "content": [
                {
                    "text": json.dumps({
                        "root_cause": "Test",
                        "confidence": 0.5,
                        "error_category": "LOKI_SEGFAULT_NULL_DEREF",
                        "severity": "MEDIUM",
                        "affected_files": [],
                    })
                }
            ],
            "usage": {"input_tokens": 100, "output_tokens": 50},
        }
        
        mock_httpx_client.post.side_effect = [
            rate_limit_response,
            success_response,
        ]
        
        # Should succeed after retry
        result = await llm_client.complete(
            system_prompt="System",
            user_prompt="User",
            response_model=RootCauseResult,
        )
        
        assert result.confidence == 0.5
        assert mock_httpx_client.post.call_count == 2
    
    @pytest.mark.asyncio
    async def test_llm_client_rate_limit_max_retries(self, llm_client, mock_httpx_client):
        """Test rate limit failure after max retries."""
        llm_client.max_retries = 2
        
        # All calls return 429
        rate_limit_response = Mock()
        rate_limit_response.status_code = 429
        rate_limit_response.headers = {"retry-after": "1"}
        
        mock_httpx_client.post.return_value = rate_limit_response
        
        # Should raise after max retries
        with pytest.raises(LLMRateLimitError):
            await llm_client.complete(
                system_prompt="System",
                user_prompt="User",
                response_model=RootCauseResult,
            )
    
    @pytest.mark.asyncio
    async def test_llm_client_validation_error(self, llm_client, mock_httpx_client):
        """Test LLM response validation failure."""
        # Mock response with invalid JSON
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "content": [
                {
                    "text": json.dumps({
                        "root_cause": "Test",
                        # Missing required fields: confidence, error_category, severity
                    })
                }
            ],
            "usage": {"input_tokens": 100, "output_tokens": 50},
        }
        mock_httpx_client.post.return_value = mock_response
        
        # Should raise validation error
        with pytest.raises(LLMValidationError):
            await llm_client.complete(
                system_prompt="System",
                user_prompt="User",
                response_model=RootCauseResult,
            )
    
    @pytest.mark.asyncio
    async def test_llm_client_http_error_retry(self, llm_client, mock_httpx_client):
        """Test retry on 500 server errors."""
        llm_client.max_retries = 2
        
        # First call: 500 error
        error_response = Mock()
        error_response.status_code = 500
        error_response.text = "Internal server error"
        error_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500", request=Mock(), response=error_response
        )
        
        # Second call: success
        success_response = Mock()
        success_response.status_code = 200
        success_response.raise_for_status.return_value = None
        success_response.json.return_value = {
            "content": [
                {
                    "text": json.dumps({
                        "root_cause": "Test",
                        "confidence": 0.6,
                        "error_category": "LOKI_SEGFAULT_NULL_DEREF",
                        "severity": "HIGH",
                        "affected_files": [],
                    })
                }
            ],
            "usage": {"input_tokens": 100, "output_tokens": 50},
        }
        
        mock_httpx_client.post.side_effect = [
            error_response,
            success_response,
        ]
        
        result = await llm_client.complete(
            system_prompt="System",
            user_prompt="User",
            response_model=RootCauseResult,
        )
        
        assert result.confidence == 0.6
        assert mock_httpx_client.post.call_count == 2
    
    @pytest.mark.asyncio
    async def test_llm_client_usage_summary(self, llm_client, mock_httpx_client):
        """Test token usage tracking."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "content": [
                {
                    "text": json.dumps({
                        "root_cause": "Test",
                        "confidence": 0.8,
                        "error_category": "LOKI_SEGFAULT_NULL_DEREF",
                        "severity": "CRITICAL",
                        "affected_files": [],
                    })
                }
            ],
            "usage": {"input_tokens": 2000, "output_tokens": 500},
        }
        mock_httpx_client.post.return_value = mock_response
        
        # Make 2 calls
        await llm_client.complete(
            system_prompt="System",
            user_prompt="User",
            response_model=RootCauseResult,
        )
        await llm_client.complete(
            system_prompt="System",
            user_prompt="User",
            response_model=RootCauseResult,
        )
        
        # Check usage summary
        summary = llm_client.get_usage_summary()
        assert summary["total_input_tokens"] == 4000
        assert summary["total_output_tokens"] == 1000
        assert summary["total_cost"] > 0


# ============================================================================
# TEST ROOT CAUSE AGENT
# ============================================================================


class TestRootCauseAgent:
    """Test RootCauseAgent orchestrator."""
    
    @pytest.fixture
    def mock_ticket(self):
        """Create mock Jira ticket."""
        return JiraTicket(
            key="TVPF-12345",
            summary="Netflix app crashes on launch",
            description="User reports Netflix crashing immediately after launch. Device: P75Q9-J01. Firmware: 9.0.12.1.",
            priority="P1",
            status="Open",
            created_at=datetime.now(timezone.utc),
        )
    
    @pytest.fixture
    def mock_pipeline_state(self, mock_ticket):
        """Create mock pipeline state."""
        from src.safs.log_analysis.models import BugLayerResult
        
        state = PipelineState(ticket=mock_ticket)
        state.buglayer_result = BugLayerResult(
            layer=BugLayer.LOKI,
            confidence=0.95,
            layer_scores={BugLayer.LOKI: 0.95},
            matched_patterns=["loki_crash_pattern"],
        )
        # Note: context_keywords would be extracted by ContextAnalyzer in real pipeline
        return state
    
    @pytest.fixture
    def mock_log_analysis_loki(self):
        """Create mock log analysis result for LOKi."""
        return LogAnalysisResult(
            drain=DrainResult(
                templates=[
                    LogTemplate(
                        id="template_1",
                        template="Application <*> crashed with signal <*>",
                        count=15,
                        examples=["Application Netflix crashed with signal SIGSEGV"],
                    ),
                    LogTemplate(
                        id="template_2",
                        template="NULL pointer dereference at <*>",
                        count=12,
                        examples=["NULL pointer dereference at 0x00000000"],
                    ),
                ],
                total_logs=1000,
                total_templates=2,
                reduction_ratio=0.97,
            ),
            enriched_lines=[],
            correlations=[
                ErrorCorrelation(
                    error1="LaunchApp failed",
                    error2="SIGSEGV",
                    count=10,
                    avg_time_diff_seconds=0.5,
                    confidence=0.89,
                ),
            ],
            incidents=[
                Incident(
                    incident_id="incident_1",
                    start_time=datetime.now(timezone.utc),
                    end_time=datetime.now(timezone.utc),
                    duration_seconds=30.0,
                    error_count=25,
                    unique_error_types={"SIGSEGV", "NULL deref"},
                    root_cause_candidates=["NULL pointer dereference"],
                    severity="HIGH",
                ),
            ],
            anomalies=[],
            cascading_failures=[],
            heuristic_root_causes=[
                "NULL pointer dereference in Loki::AppLauncher::Launch (confidence: 0.85)",
            ],
            loki_symbolication=LokiSymbolicationResult(
                load_map=[],
                raw_frames=[],
                symbolicated_frames=[
                    SymbolicatedFrame(
                        frame_number=0,
                        library_name="libloki_core.so",
                        virtual_pc=0x7f8a51a4,
                        file_offset=0x51a4,
                        function_name="Loki::AppLauncher::Launch",
                        file_name="AppLauncher.cpp",
                        line_number=142,
                        status="OK",
                    ),
                ],
                symbolication_success_rate=1.0,
            ),
        )
    
    @pytest.fixture
    def mock_log_analysis_html5(self):
        """Create mock log analysis result for HTML5."""
        return LogAnalysisResult(
            drain=DrainResult(
                templates=[], total_logs=500, total_templates=0, reduction_ratio=0.90
            ),
            enriched_lines=[],
            correlations=[],
            incidents=[],
            anomalies=[],
            cascading_failures=[],
            heuristic_root_causes=[
                "CompanionLib timing race: getVersion() called before init (confidence: 0.78)",
            ],
            cdp_analysis=CDPParseResult(
                events=[],
                exceptions=[
                    CDPException(
                        timestamp=datetime.now(timezone.utc),
                        exception_type="TypeError",
                        message="Cannot read property 'getVersion' of undefined",
                        url="https://netflix.com/VideoPlayer.js",
                        line_number=142,
                        column_number=12,
                        stack_trace=["at VideoPlayer.init (VideoPlayer.js:142:12)"],
                    ),
                ],
                console_errors=[],
                network_errors=[],
            ),
            source_mapped_frames=[
                SourceMappedFrame(
                    minified_file="bundle.min.js",
                    minified_line=1,
                    minified_column=1234,
                    original_position=SourceMapPosition(
                        original_file="VideoPlayer.js",
                        original_line=142,
                        original_column=12,
                    ),
                    status="OK",
                ),
            ],
        )
    
    @pytest.fixture
    def mock_log_analysis_mediatek(self):
        """Create mock log analysis result for MediaTek."""
        return LogAnalysisResult(
            drain=DrainResult(
                templates=[], total_logs=300, total_templates=0, reduction_ratio=0.85
            ),
            enriched_lines=[],
            correlations=[],
            incidents=[],
            anomalies=[],
            cascading_failures=[],
            heuristic_root_causes=[
                "VDEC driver NULL deref in mtk_vdec_decode (confidence: 0.82)",
            ],
            mediatek_analysis=MediaTekKernelResult(
                oops_list=[
                    KernelOops(
                        timestamp=datetime.now(timezone.utc),
                        oops_type="NULL_DEREF",
                        faulting_address=0x00000000,
                        instruction_pointer=0xc01a4b20,
                        call_trace=[
                            "mtk_vdec_decode+0x120/0x2a0",
                            "v4l2_m2m_request_queue+0x80/0x140",
                        ],
                        tainted=False,
                        subsystem="VDEC",
                    ),
                ],
                hardware_errors=[],
                subsystem_classification={"VDEC": 1},
            ),
        )
    
    @pytest.mark.asyncio
    async def test_agent_initialization(self):
        """Test RootCauseAgent initialization."""
        with patch("src.safs.root_cause_analysis.agent.LLMClient"):
            agent = RootCauseAgent(api_key="test-key", model="claude-haiku", temperature=0.0)
            assert agent.model == "claude-haiku"
            assert agent.temperature == 0.0
    
    @pytest.mark.asyncio
    async def test_agent_analyze_loki(
        self, mock_pipeline_state, mock_log_analysis_loki
    ):
        """Test root cause analysis for LOKi layer."""
        # Mock LLM client
        mock_llm = AsyncMock(spec=LLMClient)
        mock_llm.complete.return_value = RootCauseResult(
            root_cause="NULL pointer dereference in AppLauncher::Launch() at AppLauncher.cpp:142",
            confidence=0.92,
            error_category=ErrorCategory.LOKI_SEGFAULT_NULL_DEREF,
            severity=MistakeSeverity.CRITICAL,
            affected_files=["AppLauncher.cpp"],
        )
        
        with patch("src.safs.root_cause_analysis.agent.LLMClient", return_value=mock_llm):
            agent = RootCauseAgent(api_key="test-key")
            agent.llm = mock_llm
            
            result = await agent.analyze(
                state=mock_pipeline_state,
                log_analysis=mock_log_analysis_loki,
            )
            
            # Verify result
            assert result.confidence == 0.92
            assert result.error_category == ErrorCategory.LOKI_SEGFAULT_NULL_DEREF
            assert "NULL pointer" in result.root_cause
            
            # Verify LLM was called with correct prompt
            mock_llm.complete.assert_called_once()
            call_kwargs = mock_llm.complete.call_args[1]
            assert call_kwargs["system_prompt"] == LOKI_RCA_SYSTEM_PROMPT
            assert "AppLauncher::Launch" in call_kwargs["user_prompt"]
            assert "Symbolicated Stack Frames" in call_kwargs["user_prompt"]
    
    @pytest.mark.asyncio
    async def test_agent_analyze_html5(
        self, mock_pipeline_state, mock_log_analysis_html5
    ):
        """Test root cause analysis for HTML5 layer."""
        from src.safs.log_analysis.models import BugLayerResult
        
        mock_pipeline_state.buglayer_result = BugLayerResult(
            layer=BugLayer.HTML5,
            confidence=0.92,
            layer_scores={BugLayer.HTML5: 0.92},
            matched_patterns=["html5_timing_pattern"],
        )
        
        mock_llm = AsyncMock(spec=LLMClient)
        mock_llm.complete.return_value = RootCauseResult(
            root_cause="CompanionLib timing race in VideoPlayer.js:142",
            confidence=0.88,
            error_category=ErrorCategory.COMPANION_LIB_TIMING,
            severity=MistakeSeverity.CRITICAL,
            affected_files=["VideoPlayer.js"],
        )
        
        with patch("src.safs.root_cause_analysis.agent.LLMClient", return_value=mock_llm):
            agent = RootCauseAgent(api_key="test-key")
            agent.llm = mock_llm
            
            result = await agent.analyze(
                state=mock_pipeline_state,
                log_analysis=mock_log_analysis_html5,
            )
            
            assert result.confidence == 0.88
            assert result.error_category == ErrorCategory.COMPANION_LIB_TIMING
            
            call_kwargs = mock_llm.complete.call_args[1]
            assert call_kwargs["system_prompt"] == HTML5_RCA_SYSTEM_PROMPT
            assert "VideoPlayer.js" in call_kwargs["user_prompt"]
            assert "TypeError" in call_kwargs["user_prompt"]
    
    @pytest.mark.asyncio
    async def test_agent_analyze_mediatek(
        self, mock_pipeline_state, mock_log_analysis_mediatek
    ):
        """Test root cause analysis for MediaTek layer."""
        from src.safs.log_analysis.models import BugLayerResult
        
        mock_pipeline_state.buglayer_result = BugLayerResult(
            layer=BugLayer.MEDIATEK,
            confidence=0.88,
            layer_scores={BugLayer.MEDIATEK: 0.88},
            matched_patterns=["mediatek_vdec_pattern"],
        )
        
        mock_llm = AsyncMock(spec=LLMClient)
        mock_llm.complete.return_value = RootCauseResult(
            root_cause="VDEC driver NULL deref in mtk_vdec_decode",
            confidence=0.86,
            error_category=ErrorCategory.MTK_VDEC_CRASH,
            severity=MistakeSeverity.CRITICAL,
            affected_files=["drivers/media/platform/mtk-vcodec/mtk_vdec.c"],
        )
        
        with patch("src.safs.root_cause_analysis.agent.LLMClient", return_value=mock_llm):
            agent = RootCauseAgent(api_key="test-key")
            agent.llm = mock_llm
            
            result = await agent.analyze(
                state=mock_pipeline_state,
                log_analysis=mock_log_analysis_mediatek,
            )
            
            assert result.confidence == 0.86
            assert result.error_category == ErrorCategory.MTK_VDEC_CRASH
            
            call_kwargs = mock_llm.complete.call_args[1]
            assert call_kwargs["system_prompt"] == MEDIATEK_RCA_SYSTEM_PROMPT
            assert "mtk_vdec_decode" in call_kwargs["user_prompt"]
            assert "VDEC" in call_kwargs["user_prompt"]
    
    @pytest.mark.asyncio
    async def test_agent_evidence_formatting_drain(
        self, mock_pipeline_state, mock_log_analysis_loki
    ):
        """Test Drain templates formatting in evidence summary."""
        mock_llm = AsyncMock(spec=LLMClient)
        mock_llm.complete.return_value = RootCauseResult(
            root_cause="Test",
            confidence=0.5,
            error_category=ErrorCategory.LOKI_SEGFAULT_NULL_DEREF,
            severity=MistakeSeverity.MEDIUM,
            affected_files=[],
        )
        
        with patch("src.safs.root_cause_analysis.agent.LLMClient", return_value=mock_llm):
            agent = RootCauseAgent(api_key="test-key")
            agent.llm = mock_llm
            
            await agent.analyze(
                state=mock_pipeline_state,
                log_analysis=mock_log_analysis_loki,
            )
            
            user_prompt = mock_llm.complete.call_args[1]["user_prompt"]
            assert "Log Templates (Drain Clustering)" in user_prompt
            assert "**Total logs**: 1000" in user_prompt
            assert "**Reduction ratio**: 97.0%" in user_prompt
            assert "Application <*> crashed with signal <*>" in user_prompt
    
    @pytest.mark.asyncio
    async def test_agent_evidence_formatting_correlations(
        self, mock_pipeline_state, mock_log_analysis_loki
    ):
        """Test temporal correlations formatting."""
        mock_llm = AsyncMock(spec=LLMClient)
        mock_llm.complete.return_value = RootCauseResult(
            root_cause="Test",
            confidence=0.5,
            error_category=ErrorCategory.LOKI_SEGFAULT_NULL_DEREF,
            severity=MistakeSeverity.MEDIUM,
            affected_files=[],
        )
        
        with patch("src.safs.root_cause_analysis.agent.LLMClient", return_value=mock_llm):
            agent = RootCauseAgent(api_key="test-key")
            agent.llm = mock_llm
            
            await agent.analyze(
                state=mock_pipeline_state,
                log_analysis=mock_log_analysis_loki,
            )
            
            user_prompt = mock_llm.complete.call_args[1]["user_prompt"]
            assert "Temporal Error Correlations" in user_prompt
            assert "LaunchApp failed" in user_prompt
            assert "SIGSEGV" in user_prompt
            assert "count=10" in user_prompt
    
    @pytest.mark.asyncio
    async def test_agent_evidence_formatting_heuristic_candidates(
        self, mock_pipeline_state, mock_log_analysis_loki
    ):
        """Test heuristic candidates formatting."""
        mock_llm = AsyncMock(spec=LLMClient)
        mock_llm.complete.return_value = RootCauseResult(
            root_cause="Test",
            confidence=0.5,
            error_category=ErrorCategory.LOKI_SEGFAULT_NULL_DEREF,
            severity=MistakeSeverity.MEDIUM,
            affected_files=[],
        )
        
        with patch("src.safs.root_cause_analysis.agent.LLMClient", return_value=mock_llm):
            agent = RootCauseAgent(api_key="test-key")
            agent.llm = mock_llm
            
            await agent.analyze(
                state=mock_pipeline_state,
                log_analysis=mock_log_analysis_loki,
            )
            
            user_prompt = mock_llm.complete.call_args[1]["user_prompt"]
            assert "Heuristic Root Cause Candidates" in user_prompt
            assert "NULL pointer dereference" in user_prompt
            assert "confidence: 0.85" in user_prompt
    
    @pytest.mark.asyncio
    async def test_agent_close(self):
        """Test agent cleanup."""
        mock_llm = AsyncMock(spec=LLMClient)
        
        with patch("src.safs.root_cause_analysis.agent.LLMClient", return_value=mock_llm):
            agent = RootCauseAgent(api_key="test-key")
            agent.llm = mock_llm
            
            await agent.close()
            mock_llm.close.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
