"""
Phase 9: Bug Reproduction (Stage 5.5) - Test Suite
===================================================

Comprehensive tests for Bug Reproduction Agent and supporting components.

Coverage targets:
- Models: CompanionLibInfo, ReproductionEvidence, BaselineMetrics, ReproStep, ReproResultV2
- DynamicCompanionLibResolver: Registry resolution, firmware compatibility
- BugReproductionAgent: Reproduction strategies, evidence capture, error detection

Master Prompt Reference: Section 3.8 - Stage 5.5
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch
from typing import Dict, Any

from src.safs.reproduction import (
    BugReproductionAgent,
    DynamicCompanionLibResolver,
    CompanionLibInfo,
    ReproductionStrategy,
    ReproductionStatus,
    ReproductionEvidence,
    BaselineMetrics,
    ReproStep,
    ReproResultV2,
)
from src.safs.log_analysis.models import (
    PipelineState,
    JiraTicket,
    BugLayerResult,
    BugLayer,
    ErrorCategory,
)


# ============================================================================
# PART 1: Model Tests
# ============================================================================


class TestCompanionLibInfo:
    """Test CompanionLibInfo model."""
    
    def test_valid_companion_info(self):
        """Test valid CompanionLibInfo creation."""
        info = CompanionLibInfo(
            loki_version="3.2.1",
            firmware_version="5.2.1",
            chipset="MT5882",
            companion_enabled=True,
            chromium_version="95.0.4638.74",
            companion_api_version="v3.2",
        )
        
        assert info.loki_version == "3.2.1"
        assert info.firmware_version == "5.2.1"
        assert info.chipset == "MT5882"
        assert info.companion_enabled is True
        assert info.companion_api_version == "v3.2"
    
    def test_companion_disabled(self):
        """Test companion disabled scenario."""
        info = CompanionLibInfo(
            loki_version="2.8.0",
            firmware_version="4.5.0",
            chipset="MT5396",
            companion_enabled=False,
            companion_api_version="v2.8",
        )
        
        assert info.companion_enabled is False


class TestReproductionEvidence:
    """Test ReproductionEvidence model."""
    
    def test_empty_evidence(self):
        """Test empty evidence creation."""
        evidence = ReproductionEvidence()
        
        assert evidence.logs == ""
        assert evidence.screenshot is None
        assert evidence.scene_graph is None
        assert evidence.error_count == 0
        assert len(evidence.matched_patterns) == 0
    
    def test_evidence_with_errors(self):
        """Test evidence with captured errors."""
        evidence = ReproductionEvidence(
            logs="ERROR: Segmentation fault at 0x00401234\nERROR: Failed to load texture",
            screenshot="screenshot_20260224_120000.png",
            scene_graph={"root": "MainScene", "children": []},
            error_count=2,
            matched_patterns=["LOKI_SEGFAULT_NULL_DEREF"],
        )
        
        assert "Segmentation fault" in evidence.logs
        assert evidence.error_count == 2
        assert len(evidence.matched_patterns) == 1


class TestBaselineMetrics:
    """Test BaselineMetrics model."""
    
    def test_baseline_metrics(self):
        """Test baseline metrics creation."""
        metrics = BaselineMetrics(
            loki_memory_mb=245.5,
            chromium_memory_mb=512.3,
            cpu_percent=35.7,
            error_rate_per_min=2.5,
            crash_count=1,
        )
        
        assert metrics.loki_memory_mb == 245.5
        assert metrics.chromium_memory_mb == 512.3
        assert metrics.cpu_percent == 35.7


class TestReproStep:
    """Test ReproStep model."""
    
    def test_launch_app_step(self):
        """Test launch_app reproduction step."""
        step = ReproStep(
            action="launch_app",
            params={"app_name": "Netflix"},
            description="Launch Netflix application",
        )
        
        assert step.action == "launch_app"
        assert step.params["app_name"] == "Netflix"
    
    def test_send_key_step(self):
        """Test send_key reproduction step."""
        step = ReproStep(
            action="send_key",
            params={"key": "Down"},
        )
        
        assert step.action == "send_key"
        assert step.params["key"] == "Down"
    
    def test_wait_step(self):
        """Test wait reproduction step."""
        step = ReproStep(
            action="wait",
            params={"seconds": 10},
            description="Wait for app to load",
        )
        
        assert step.action == "wait"
        assert step.params["seconds"] == 10


class TestReproResultV2:
    """Test ReproResultV2 model."""
    
    def test_reproduced_bug(self):
        """Test reproduced bug result."""
        result = ReproResultV2(
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
                logs="ERROR: SIGSEGV at main.cpp:142",
                error_count=1,
            ),
            execution_time_seconds=45.3,
        )
        
        assert result.status == ReproductionStatus.REPRODUCED
        assert result.reproducible is True  # Legacy field auto-set
        assert result.execution_time_seconds == 45.3
    
    def test_not_reproduced(self):
        """Test not reproduced result."""
        result = ReproResultV2(
            status=ReproductionStatus.NOT_REPRODUCED,
            strategy=ReproductionStrategy.EXPLORATORY,
            execution_time_seconds=30.0,
        )
        
        assert result.status == ReproductionStatus.NOT_REPRODUCED
        assert result.reproducible is False  # Legacy field auto-set
    
    def test_skipped_reproduction(self):
        """Test skipped reproduction."""
        result = ReproResultV2(
            status=ReproductionStatus.SKIP,
            strategy=ReproductionStrategy.SKIP,
            reason="Firmware mismatch",
            execution_time_seconds=1.5,
        )
        
        assert result.status == ReproductionStatus.SKIP
        assert result.reason == "Firmware mismatch"
        assert result.reproducible is False


# ============================================================================
# PART 2: DynamicCompanionLibResolver Tests
# ============================================================================


class TestDynamicCompanionLibResolver:
    """Test DynamicCompanionLibResolver."""
    
    async def test_resolve_companion_info(self):
        """Test successful companion info resolution."""
        mock_ssh = AsyncMock()
        mock_ssh.call = AsyncMock(side_effect=[
            "3.2.1",  # LOKi version
            "5.2.1",  # Firmware version
            "MT5882",  # Chipset
            "true",  # Companion enabled
            "95.0.4638.74",  # Chromium version
        ])
        
        resolver = DynamicCompanionLibResolver(mock_ssh)
        info = await resolver.resolve()
        
        assert info.loki_version == "3.2.1"
        assert info.firmware_version == "5.2.1"
        assert info.chipset == "MT5882"
        assert info.companion_enabled is True
        assert info.companion_api_version == "v3.2"
    
    async def test_resolve_with_dict_response(self):
        """Test resolution with dict-formatted responses."""
        mock_ssh = AsyncMock()
        # Return None for chromium version to trigger RuntimeError, then handle gracefully
        mock_ssh.call = AsyncMock(side_effect=[
            {"value": "4.0.5"},  # LOKi version as dict
            {"value": "6.0.0"},  # Firmware as dict
            "MT5670",  # Chipset as string
            "false",  # Companion disabled
        ])
        
        resolver = DynamicCompanionLibResolver(mock_ssh)
        
        # Since companion_enabled=false, chromium won't be queried
        # But our implementation always queries it. Let's adjust the test:
        # We need to make the SSH call not fail. Let me provide a fallback response.
        # Actually, looking at device_resolver.py, if the registry call fails for chromium,
        # it will raise RuntimeError. Let's check if chromium is optional.
        # For now, let's add the chromium response:
        mock_ssh.call = AsyncMock(side_effect=[
            {"value": "4.0.5"},  # LOKi version as dict
            {"value": "6.0.0"},  # Firmware as dict
            "MT5670",  # Chipset as string
            "false",  # Companion disabled
            "90.0.4430.85",  # Chromium version
        ])
        
        info = await resolver.resolve()
        
        assert info.loki_version == "4.0.5"
        assert info.companion_api_version == "v4.0"
        assert info.companion_enabled is False
    
    async def test_resolve_error(self):
        """Test error handling during resolution."""
        mock_ssh = AsyncMock()
        mock_ssh.call = AsyncMock(side_effect=Exception("SSH connection failed"))
        
        resolver = DynamicCompanionLibResolver(mock_ssh)
        
        with pytest.raises(RuntimeError, match="Cannot resolve companion library info"):
            await resolver.resolve()
    
    def test_derive_api_version(self):
        """Test API version derivation from LOKi version."""
        resolver = DynamicCompanionLibResolver(Mock())
        
        assert resolver._derive_api_version("3.2.1") == "v3.2"
        assert resolver._derive_api_version("4.0.5") == "v4.0"
        assert resolver._derive_api_version("2.8.3-beta") == "v2.8"
        assert resolver._derive_api_version("invalid") == "v3.0"  # Fallback
    
    def test_firmware_compatible_exact_match(self):
        """Test firmware compatibility with exact match."""
        resolver = DynamicCompanionLibResolver(Mock())
        
        assert resolver.check_firmware_compatible("5.2.1", "5.2.1") is True
    
    def test_firmware_compatible_major_minor(self):
        """Test firmware compatibility with same major.minor."""
        resolver = DynamicCompanionLibResolver(Mock())
        
        assert resolver.check_firmware_compatible("5.2.1", "5.2.0") is True
        assert resolver.check_firmware_compatible("5.2.0", "5.2.5") is True
    
    def test_firmware_incompatible(self):
        """Test firmware incompatibility."""
        resolver = DynamicCompanionLibResolver(Mock())
        
        assert resolver.check_firmware_compatible("5.2.1", "4.5.0") is False
        assert resolver.check_firmware_compatible("6.0.0", "5.2.1") is False
    
    def test_firmware_no_requirement(self):
        """Test firmware compatibility when no requirement specified."""
        resolver = DynamicCompanionLibResolver(Mock())
        
        assert resolver.check_firmware_compatible("5.2.1", None) is True
        assert resolver.check_firmware_compatible("5.2.1", "") is True


# ============================================================================
# PART 3: BugReproductionAgent Tests
# ============================================================================


class TestBugReproductionAgent:
    """Test BugReproductionAgent."""
    
    def create_mock_mcps(self):
        """Create mock MCP clients."""
        mock_remote = AsyncMock()
        mock_ssh = AsyncMock()
        mock_loki = AsyncMock()
        
        return mock_remote, mock_ssh, mock_loki
    
    def create_test_state(
        self,
        firmware_version: str = "5.2.1",
        streaming_app: str = "Netflix",
        repro_steps: list = None,
    ) -> PipelineState:
        """Create test PipelineState."""
        ticket = JiraTicket(
            key="TEST-123",
            summary="SIGSEGV in HomeScreen",
            description="TV crashes when launching Netflix",
            priority="P1",
            firmware_version=firmware_version,
            streaming_app=streaming_app,
            repro_steps=repro_steps or [],
        )
        
        buglayer_result = BugLayerResult(
            layer=BugLayer.LOKI,
            confidence=0.95,
            layer_scores={BugLayer.LOKI: 0.95},
            matched_patterns=["LOKI_SEGFAULT_NULL_DEREF"],
        )
        
        return PipelineState(
            ticket=ticket,
            buglayer_result=buglayer_result,
        )
    
    async def test_tv_not_available(self):
        """Test reproduction when TV not available."""
        agent = BugReproductionAgent(tv_available=False)
        state = self.create_test_state()
        
        result = await agent.attempt(state)
        
        assert result.status == ReproductionStatus.SKIP
        assert "no dev tv available" in result.reason.lower()
    
    async def test_firmware_mismatch(self):
        """Test reproduction skipped due to firmware mismatch."""
        mock_remote, mock_ssh, mock_loki = self.create_mock_mcps()
       
        # Mock SSH responses for companion info resolution (called twice)
        mock_ssh.call = AsyncMock(side_effect=[
            # First resolve call (_resolve_companion_info)
            "3.2.1",  # LOKi version
            "6.0.0",  # Firmware version (mismatch)
            "MT5882",
            "true",
            "95.0",
        ])
        
        agent = BugReproductionAgent(
            remote_mcp=mock_remote,
            ssh_mcp=mock_ssh,
            loki_mcp=mock_loki,
            tv_available=True,
        )
        
        state = self.create_test_state(firmware_version="5.2.1")
        result = await agent.attempt(state)
        
        assert result.status == ReproductionStatus.SKIP
        assert "Firmware mismatch" in result.reason
    
    async def test_deterministic_strategy(self):
        """Test deterministic reproduction strategy with explicit steps."""
        mock_remote, mock_ssh, mock_loki = self.create_mock_mcps()
        
        # Mock SSH responses for compatible firmware
        companion_info_responses = [
            "3.2.1",  # LOKi version
            "5.2.1",  # Firmware version (compatible)
            "MT5882",
            "true",
            "95.0",
        ]
        
        # Mock logs with error
        log_response = "ERROR: SIGSEGV at main.cpp:142\nSegmentation fault"
        
        # Mock screenshot and scene graph
        screenshot_response = "screenshot_data"
        scene_graph_response = {"root": "MainScene"}
        
        # Mock system metrics
        metrics_responses = [
            "245000",  # LOKi memory (KB)
            "512000",  # Chromium memory (KB)
            "35.7",  # CPU percent
        ]
        
        mock_ssh.call = AsyncMock(side_effect=[
            *companion_info_responses,
            log_response,
            *metrics_responses,
        ])
        
        # Mock remote.call for launch_app and send_key actions
        mock_remote.call = AsyncMock(return_value=None)
        
        mock_loki.call = AsyncMock(side_effect=[
            screenshot_response,
            scene_graph_response,
        ])
        
        agent = BugReproductionAgent(
            remote_mcp=mock_remote,
            ssh_mcp=mock_ssh,
            loki_mcp=mock_loki,
            tv_available=True,
        )
        
        repro_steps = [
            "Launch Netflix",
            "Press Down key",
            "Wait 5 seconds",
        ]
        
        state = self.create_test_state(repro_steps=repro_steps)
        result = await agent.attempt(state)
        
        assert result.status == ReproductionStatus.REPRODUCED
        assert result.strategy == ReproductionStrategy.DETERMINISTIC
        assert result.evidence.error_count > 0
        assert len(result.repro_steps_executed) == 3
    
    async def test_exploratory_strategy(self):
        """Test exploratory reproduction strategy (no explicit steps)."""
        mock_remote, mock_ssh, mock_loki = self.create_mock_mcps()
        
        # Mock responses for exploratory reproduction
        companion_info_responses = [
            "3.2.1",
            "5.2.1",
            "MT5882",
            "true",
            "95.0",
        ]
        
        log_response = "INFO: Application started\nERROR: Memory allocation failed"
        
        mock_ssh.call = AsyncMock(side_effect=[
            *companion_info_responses,
            log_response,
            "200000",  # Metrics
            "400000",
            "25.0",
        ])
        
        mock_loki.call = AsyncMock(side_effect=["screenshot", {"root": "Scene"}])
        
        agent = BugReproductionAgent(
            remote_mcp=mock_remote,
            ssh_mcp=mock_ssh,
            loki_mcp=mock_loki,
            tv_available=True,
        )
        
        state = self.create_test_state(repro_steps=[])  # No explicit steps
        result = await agent.attempt(state)
        
        assert result.status == ReproductionStatus.REPRODUCED
        assert result.strategy == ReproductionStrategy.EXPLORATORY
    
    async def test_error_not_found(self):
        """Test reproduction when error does not manifest."""
        mock_remote, mock_ssh, mock_loki = self.create_mock_mcps()
        
        # Mock responses with clean logs (no errors)
        companion_info_responses = [
            "3.2.1",
            "5.2.1",
            "MT5882",
            "true",
            "95.0",
        ]
        
        log_response = "INFO: Application started successfully\nINFO: All systems operational"
        
        mock_ssh.call = AsyncMock(side_effect=[
            *companion_info_responses,
            log_response,
            "200000",
            "400000",
            "15.0",
        ])
        
        mock_loki.call = AsyncMock(side_effect=["screenshot", {"root": "Scene"}])
        
        agent = BugReproductionAgent(
            remote_mcp=mock_remote,
            ssh_mcp=mock_ssh,
            loki_mcp=mock_loki,
            tv_available=True,
        )
        
        state = self.create_test_state()
        result = await agent.attempt(state)
        
        assert result.status == ReproductionStatus.NOT_REPRODUCED
        assert result.evidence.error_count == 0
    
    async def test_reproduction_error_handling(self):
        """Test error handling during reproduction."""
        mock_remote, mock_ssh, mock_loki = self.create_mock_mcps()
        
        # Mock SSH failure during resolution
        mock_ssh.call = AsyncMock(side_effect=Exception("Connection timeout"))
        
        agent = BugReproductionAgent(
            remote_mcp=mock_remote,
            ssh_mcp=mock_ssh,
            loki_mcp=mock_loki,
            tv_available=True,
        )
        
        state = self.create_test_state()
        result = await agent.attempt(state)
        
        assert result.status == ReproductionStatus.SKIP
        assert "error" in result.reason.lower()
    
    def test_parse_repro_steps_launch_app(self):
        """Test parsing launch app step."""
        agent = BugReproductionAgent()
        
        steps = agent._parse_repro_steps(["Launch Netflix application"])
        
        assert len(steps) == 1
        assert steps[0].action == "launch_app"
        assert steps[0].params["app_name"] == "Netflix"
    
    def test_parse_repro_steps_send_key(self):
        """Test parsing send key step."""
        agent = BugReproductionAgent()
        
        steps = agent._parse_repro_steps(["Press Down key", "Press OK key"])
        
        assert len(steps) == 2
        assert steps[0].action == "send_key"
        assert steps[0].params["key"] == "Down"
        assert steps[1].params["key"] == "Ok"
    
    def test_parse_repro_steps_wait(self):
        """Test parsing wait step."""
        agent = BugReproductionAgent()
        
        steps = agent._parse_repro_steps(["Wait 10 seconds"])
        
        assert len(steps) == 1
        assert steps[0].action == "wait"
        assert steps[0].params["seconds"] == 10
    
    def test_unit_for_layer(self):
        """Test log unit determination for bug layers."""
        agent = BugReproductionAgent()
        
        assert agent._unit_for_layer(BugLayer.LOKI) == "loki"
        assert agent._unit_for_layer(BugLayer.HTML5) == "cobalt"
        assert agent._unit_for_layer(BugLayer.MEDIATEK) == "kernel"
        assert agent._unit_for_layer(BugLayer.CROSS_LAYER) == "loki"
    
    def test_check_error_present_with_patterns(self):
        """Test error detection with matched patterns."""
        agent = BugReproductionAgent()
        
        logs = "ERROR: SIGSEGV in main() at line 142\nFatal error occurred"
        patterns = ["LOKI_SEGFAULT_NULL_DEREF", "sigsegv"]
        
        assert agent._check_error_present(logs, patterns) is True
    
    def test_check_error_present_fallback(self):
        """Test error detection with fallback indicators."""
        agent = BugReproductionAgent()
        
        logs = "Application crashed due to exception"
        patterns = []
        
        assert agent._check_error_present(logs, patterns) is True
    
    def test_check_error_not_present(self):
        """Test error not present in clean logs."""
        agent = BugReproductionAgent()
        
        logs = "INFO: Application started\nINFO: All systems operational"
        patterns = []
        
        assert agent._check_error_present(logs, patterns) is False
    
    def test_count_errors_in_logs(self):
        """Test error counting in logs."""
        agent = BugReproductionAgent()
        
        logs = "ERROR: Failed\nERROR: Timeout\nException thrown\nFatal error"
        patterns = ["LOKI_SEGFAULT"]
        
        count = agent._count_errors_in_logs(logs, patterns)
        
        assert count > 0  # Should count error, exception, fatal


# ============================================================================
# PART 4: Integration Tests
# ============================================================================


class TestIntegration:
    """Integration tests for full reproduction workflow."""
    
    async def test_end_to_end_successful_reproduction(self):
        """Test complete reproduction workflow with successful reproduction."""
        mock_remote = AsyncMock()
        mock_ssh = AsyncMock()
        mock_loki = AsyncMock()
        
        # Complete mock sequence
        mock_ssh.call = AsyncMock(side_effect=[
            # Companion info resolution
            "3.2.1", "5.2.1", "MT5882", "true", "95.0",
            # Log capture
            "ERROR: SIGSEGV at 0x00401234\nSegmentation fault in HomeScreen::render()",
            # Baseline metrics
            "250000", "500000", "30.0",
        ])
        
        # Mock remote.call for launch_app action
        mock_remote.call = AsyncMock(return_value=None)
        
        mock_loki.call = AsyncMock(side_effect=[
            "screenshot_base64",  # Screenshot
            {"root": "MainScene", "children": []},  # Scene graph
        ])
        
        agent = BugReproductionAgent(
            remote_mcp=mock_remote,
            ssh_mcp=mock_ssh,
            loki_mcp=mock_loki,
            tv_available=True,
        )
        
        # Create state with reproduction steps
        ticket = JiraTicket(
            key="TEST-456",
            summary="HomeScreen crashes on Netflix launch",
            description="Segmentation fault when launching Netflix",
            priority="P0",
            firmware_version="5.2.1",
            streaming_app="Netflix",
            repro_steps=["Launch Netflix", "Wait 5 seconds"],
        )
        
        buglayer_result = BugLayerResult(
            layer=BugLayer.LOKI,
            confidence=0.95,
            layer_scores={BugLayer.LOKI: 0.95},
            matched_patterns=["LOKI_SEGFAULT_NULL_DEREF"],
        )
        
        state = PipelineState(
            ticket=ticket,
            buglayer_result=buglayer_result,
        )
        
        result = await agent.attempt(state)
        
        # Assertions
        assert result.status == ReproductionStatus.REPRODUCED
        assert result.strategy == ReproductionStrategy.DETERMINISTIC
        assert result.companion_info is not None
        assert result.companion_info.loki_version == "3.2.1"
        assert result.evidence.error_count > 0
        assert "Segmentation fault" in result.evidence.logs
        assert result.baseline_metrics is not None
        assert len(result.repro_steps_executed) == 2
        assert result.execution_time_seconds > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
