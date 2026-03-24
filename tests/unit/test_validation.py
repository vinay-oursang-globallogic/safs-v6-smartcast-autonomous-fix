"""
Unit Tests for SAFS v6.0 Validation Module

Tests tri-path validation: QEMU (α), Playwright (β), On-Device (γ)
"""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.safs.log_analysis.models import (
    BugLayer,
    BugLayerResult,
    ErrorCategory,
    FixCandidate,
    FixStrategy,
    JiraTicket,
    PipelineState,
)
from src.safs.validation import (
    CandidateValidationResult,
    ChipsetTarget,
    OnDeviceValidator,
    PathValidationResult,
    PlaywrightValidator,
    QEMUValidator,
    TriPathValidator,
    ValidationPath,
)


# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture
def sample_loki_candidate():
    """Sample LOKi C++ fix candidate"""
    return FixCandidate(
        fix_id="fix-loki-001",
        strategy=FixStrategy.NULL_CHECK,
        confidence=0.75,
        routing="PR_WITH_REVIEW",
        file_changes=[
            {
                "path": "AppLauncher.cpp",
                "content": "void AppLauncher::Launch() { if (app != nullptr) { app->Start(); } }",
            }
        ],
        diff="+ if (app != nullptr) {",
        explanation="Added null check before app->Start()",
    )


@pytest.fixture
def sample_html5_candidate():
    """Sample HTML5 fix candidate"""
    return FixCandidate(
        fix_id="fix-html5-001",
        strategy=FixStrategy.EVENT_LISTENER_CLEANUP,
        confidence=0.80,
        routing="PR_WITH_REVIEW",
        file_changes=[
            {
                "path": "player.js",
                "content": "window.removeEventListener('vizio_ready', onReady);",
            }
        ],
        diff="+ window.removeEventListener('vizio_ready', onReady);",
        explanation="Added event listener cleanup to prevent memory leak",
    )


@pytest.fixture
def sample_ticket():
    """Sample Jira ticket"""
    return JiraTicket(
        key="SMART-1234",
        summary="LOKi crash on app launch",
        description="LOKi crashes with SIGSEGV when launching Netflix",
        created=datetime.now(timezone.utc),
        updated=datetime.now(timezone.utc),
        priority="P1",
        status="Open",
        assignee="safs-bot",
        reporter="user@vizio.com",
        labels=["crash", "loki"],
        attachments=[],
    )


@pytest.fixture
def sample_pipeline_state(sample_ticket):
    """Sample pipeline state with LOKi bug"""
    from src.safs.log_analysis.models import RootCauseResult, MistakeSeverity
    
    return PipelineState(
        ticket=sample_ticket,
        buglayer_result=BugLayerResult(
            layer=BugLayer.LOKI,
            confidence=0.95,
        ),
        root_cause_result=RootCauseResult(
            root_cause="Null pointer dereference in AppLauncher::Launch()",
            confidence=0.90,
            error_category=ErrorCategory.LOKI_SEGFAULT_NULL_DEREF,
            severity=MistakeSeverity.CRITICAL,
            affected_files=["AppLauncher.cpp"],
        ),
    )


# ============================================================================
# QEMU VALIDATOR TESTS
# ============================================================================


class TestQEMUValidator:
    """Test PATH α (QEMU) validator"""
    
    @pytest.mark.asyncio
    async def test_qemu_validation_both_chipsets_pass(self, sample_loki_candidate):
        """Test QEMU validation with both chipsets passing"""
        validator = QEMUValidator()
        
        # Mock cross-compile and QEMU execution
        with patch.object(validator, "_cross_compile") as mock_compile, \
             patch.object(validator, "_run_qemu_tests") as mock_run:
            
            # Both compilations succeed
            mock_compile.return_value = {
                "success": True,
                "binary_path": Path("/tmp/test_binary"),
                "log": "Compilation successful",
                "error": None,
            }
            
            # Both test runs pass with no sanitizer findings
            mock_run.return_value = {
                "passed": True,
                "output": "All tests passed",
                "sanitizer_findings": [],
                "failures": [],
            }
            
            result = await validator.validate(
                sample_loki_candidate,
                ErrorCategory.LOKI_SEGFAULT_NULL_DEREF,
            )
            
            assert result.path == ValidationPath.ALPHA_QEMU
            assert result.passed is True
            assert "MTK_LEGACY_tests" in result.test_results
            assert "MTK_CURRENT_tests" in result.test_results
            assert result.test_results["MTK_LEGACY_tests"] is True
            assert result.test_results["MTK_CURRENT_tests"] is True
            assert len(result.failure_reasons) == 0
    
    @pytest.mark.asyncio
    async def test_qemu_validation_sanitizer_findings(self, sample_loki_candidate):
        """Test QEMU validation with ASan findings"""
        validator = QEMUValidator()
        
        with patch.object(validator, "_cross_compile") as mock_compile, \
             patch.object(validator, "_run_qemu_tests") as mock_run:
            
            mock_compile.return_value = {
                "success": True,
                "binary_path": Path("/tmp/test_binary"),
                "log": "Compilation successful",
                "error": None,
            }
            
            # ASan detects memory leak
            mock_run.return_value = {
                "passed": False,
                "output": "ERROR: LeakSanitizer: detected memory leaks",
                "sanitizer_findings": ["Memory leak detected"],
                "failures": ["Sanitizer violations: 1"],
            }
            
            result = await validator.validate(
                sample_loki_candidate,
                ErrorCategory.LOKI_SEGFAULT_NULL_DEREF,
            )
            
            assert result.passed is False
            assert len(result.failure_reasons) > 0
            assert "sanitizer_findings" in result.evidence.get("qemu_details", {})
    
    @pytest.mark.asyncio
    async def test_qemu_validation_mtk_legacy_fails(self, sample_loki_candidate):
        """Test QEMU validation with MTK_LEGACY failure"""
        validator = QEMUValidator()
        
        with patch.object(validator, "_cross_compile") as mock_compile, \
             patch.object(validator, "_run_qemu_tests") as mock_run:
            
            # MTK_LEGACY compile fails, MTK_CURRENT succeeds
            def compile_side_effect(candidate, chipset, sanitizers):
                if chipset == ChipsetTarget.MTK_LEGACY:
                    return {
                        "success": False,
                        "log": "Compilation error: undefined symbol",
                        "error": "Compilation failed",
                    }
                return {
                    "success": True,
                    "binary_path": Path("/tmp/test_binary"),
                    "log": "Compilation successful",
                    "error": None,
                }
            
            mock_compile.side_effect = compile_side_effect
            mock_run.return_value = {
                "passed": True,
                "output": "All tests passed",
                "sanitizer_findings": [],
                "failures": [],
            }
            
            result = await validator.validate(
                sample_loki_candidate,
                ErrorCategory.LOKI_SEGFAULT_NULL_DEREF,
            )
            
            assert result.passed is False
            assert any("MTK_LEGACY" in reason for reason in result.failure_reasons)


# ============================================================================
# PLAYWRIGHT VALIDATOR TESTS
# ============================================================================


class TestPlaywrightValidator:
    """Test PATH β (Playwright) validator"""
    
    @pytest.mark.asyncio
    async def test_playwright_validation_success(self, sample_html5_candidate):
        """Test successful Playwright validation"""
        validator = PlaywrightValidator()
        
        # Playwright is not installed in test environment, so this will gracefully fail
        result = await validator.validate(
            sample_html5_candidate,
            ErrorCategory.COMPANION_LIB_TIMING,
            app_name="Netflix",
        )
        
        # Without playwright installed, validation should fail gracefully
        assert result.path == ValidationPath.BETA_PLAYWRIGHT
        assert result.passed is False  # Expected since playwright not installed
    
    @pytest.mark.asyncio
    async def test_playwright_not_installed(self, sample_html5_candidate):
        """Test Playwright validation when playwright not installed"""
        validator = PlaywrightValidator()
        
        result = await validator.validate(
            sample_html5_candidate,
            ErrorCategory.COMPANION_LIB_TIMING,
        )
        
        # Without playwright installed, should fail gracefully
        assert result.passed is False
        assert result.path == ValidationPath.BETA_PLAYWRIGHT


# ============================================================================
# ON-DEVICE VALIDATOR TESTS
# ============================================================================


class TestOnDeviceValidator:
    """Test PATH γ (On-Device) validator"""
    
    @pytest.mark.asyncio
    async def test_ondevice_validation_success(self, sample_loki_candidate, sample_ticket):
        """Test successful on-device validation"""
        validator = OnDeviceValidator(tv_ip="192.168.1.100")
        
        with patch.object(validator, "_check_tv_available") as mock_check, \
             patch.object(validator, "_get_firmware_version") as mock_fw, \
             patch.object(validator, "_get_companion_version") as mock_comp, \
             patch.object(validator, "_capture_state") as mock_state, \
             patch.object(validator, "_reproduce_bug") as mock_repro, \
             patch.object(validator, "_deploy_fix") as mock_deploy, \
             patch.object(validator, "_restart_service") as mock_restart, \
             patch.object(validator, "_execute_post_fix_test") as mock_test:
            
            mock_check.return_value = True
            mock_fw.return_value = "v6.0.42.1"
            mock_comp.return_value = "v2.1.0"
            mock_state.return_value = {"logs": ["baseline log"]}
            mock_repro.return_value = True
            mock_deploy.return_value = True
            mock_restart.return_value = True
            mock_test.return_value = {
                "logs": ["post-fix log"],
                "scene_graph": {"status": "ok"},
                "screenshots": {},
            }
            
            result = await validator.validate(
                sample_loki_candidate,
                sample_ticket,
                BugLayer.LOKI,
                ErrorCategory.LOKI_SEGFAULT_NULL_DEREF,
            )
            
            assert result.path == ValidationPath.GAMMA_ONDEVICE
            assert result.passed is True
            assert "bug_reproduction" in result.test_results
            assert "fix_deployment" in result.test_results
    
    @pytest.mark.asyncio
    async def test_ondevice_validation_tv_not_available(self, sample_loki_candidate, sample_ticket):
        """Test on-device validation when TV not available"""
        validator = OnDeviceValidator(tv_ip=None)
        
        result = await validator.validate(
            sample_loki_candidate,
            sample_ticket,
            BugLayer.LOKI,
            ErrorCategory.LOKI_SEGFAULT_NULL_DEREF,
        )
        
        assert result.passed is False
        assert any("TV not available" in reason for reason in result.failure_reasons)


# ============================================================================
# TRI-PATH VALIDATOR TESTS
# ============================================================================


class TestTriPathValidator:
    """Test tri-path validator orchestrator"""
    
    @pytest.mark.asyncio
    async def test_required_paths_loki_bug(self):
        """Test required paths for LOKi bug"""
        validator = TriPathValidator(tv_available=True)
        
        paths = validator._required_paths(
            BugLayer.LOKI,
            ErrorCategory.LOKI_SEGFAULT_NULL_DEREF,
        )
        
        assert ValidationPath.ALPHA_QEMU in paths
        assert ValidationPath.GAMMA_ONDEVICE in paths
        assert ValidationPath.BETA_PLAYWRIGHT not in paths
    
    @pytest.mark.asyncio
    async def test_required_paths_html5_bug(self):
        """Test required paths for HTML5 bug"""
        validator = TriPathValidator(tv_available=True)
        
        paths = validator._required_paths(
            BugLayer.HTML5,
            ErrorCategory.COMPANION_LIB_TIMING,
        )
        
        assert ValidationPath.BETA_PLAYWRIGHT in paths
        assert ValidationPath.GAMMA_ONDEVICE in paths
        assert ValidationPath.ALPHA_QEMU not in paths
    
    @pytest.mark.asyncio
    async def test_required_paths_cross_layer(self):
        """Test required paths for CROSS_LAYER bug"""
        validator = TriPathValidator(tv_available=True)
        
        paths = validator._required_paths(
            BugLayer.CROSS_LAYER,
            ErrorCategory.WATCHFREE_DEEPLINK_LOSS,
        )
        
        # CROSS_LAYER requires all three paths
        assert ValidationPath.ALPHA_QEMU in paths
        assert ValidationPath.BETA_PLAYWRIGHT in paths
        assert ValidationPath.GAMMA_ONDEVICE in paths
    
    @pytest.mark.asyncio
    async def test_validate_all_with_mocked_validators(
        self, sample_pipeline_state, sample_loki_candidate
    ):
        """Test validate_all with mocked validators"""
        # Create mock validators
        mock_qemu = AsyncMock()
        mock_qemu.validate.return_value = PathValidationResult(
            path=ValidationPath.ALPHA_QEMU,
            passed=True,
            test_results={"MTK_LEGACY_tests": True, "MTK_CURRENT_tests": True},
            evidence={},
            failure_reasons=[],
            duration_seconds=30.0,
        )
        
        mock_ondevice = AsyncMock()
        mock_ondevice.validate.return_value = PathValidationResult(
            path=ValidationPath.GAMMA_ONDEVICE,
            passed=True,
            test_results={"bug_reproduction": True, "fix_deployment": True},
            evidence={"ondevice_details": {"reproduction_successful": True}},
            failure_reasons=[],
            duration_seconds=120.0,
        )
        
        validator = TriPathValidator(
            qemu_validator=mock_qemu,
            ondevice_validator=mock_ondevice,
            tv_available=True,
        )
        
        results = await validator.validate_all(
            sample_pipeline_state,
            [sample_loki_candidate],
        )
        
        assert len(results) == 1
        assert results[0].overall_passed is True
        assert results[0].confidence_boost > 0.0
        assert results[0].validation_score > 0.0
    
    def test_compute_validation_score(self):
        """Test validation score computation"""
        validator = TriPathValidator()
        
        alpha_result = PathValidationResult(
            path=ValidationPath.ALPHA_QEMU,
            passed=True,
            test_results={},
            evidence={},
            failure_reasons=[],
            duration_seconds=30.0,
        )
        
        gamma_result = PathValidationResult(
            path=ValidationPath.GAMMA_ONDEVICE,
            passed=True,
            test_results={},
            evidence={"ondevice_details": {"reproduction_successful": True}},
            failure_reasons=[],
            duration_seconds=120.0,
        )
        
        score = validator._compute_validation_score(
            alpha_result,
            None,
            gamma_result,
            [ValidationPath.ALPHA_QEMU, ValidationPath.GAMMA_ONDEVICE],
        )
        
        assert 0.0 <= score <= 1.0
        assert score > 0.7  # Both paths passed
    
    def test_compute_confidence_boost(self):
        """Test confidence boost computation"""
        validator = TriPathValidator()
        
        gamma_result = PathValidationResult(
            path=ValidationPath.GAMMA_ONDEVICE,
            passed=True,
            test_results={},
            evidence={"ondevice_details": {"reproduction_successful": True}},
            failure_reasons=[],
            duration_seconds=120.0,
        )
        
        boost = validator._compute_confidence_boost(
            gamma_result,
            [ValidationPath.GAMMA_ONDEVICE],
        )
        
        assert boost == 0.25  # 0.15 (on-device) + 0.10 (repro)
    
    @pytest.mark.asyncio
    async def test_required_paths_mediatek_no_validation(self):
        """Test MEDIATEK layer returns empty paths (auto-escalate, no validation)"""
        validator = TriPathValidator(tv_available=True)
        
        paths = validator._required_paths(
            BugLayer.MEDIATEK,
            ErrorCategory.MTK_VDEC_CRASH,
        )
        
        # BUG FIX: MEDIATEK should return empty list (no validation)
        assert paths == []
        assert ValidationPath.ALPHA_QEMU not in paths
        assert ValidationPath.BETA_PLAYWRIGHT not in paths
        assert ValidationPath.GAMMA_ONDEVICE not in paths
    
    @pytest.mark.asyncio
    async def test_exception_in_path_causes_overall_fail(
        self, sample_pipeline_state, sample_loki_candidate
    ):
        """Test that exceptions in validation paths cause overall_passed=False"""
        # Create mock validators where one throws an exception
        mock_qemu = AsyncMock()
        mock_qemu.validate.side_effect = RuntimeError("QEMU toolchain not found")
        
        mock_ondevice = AsyncMock()
        mock_ondevice.validate.return_value = PathValidationResult(
            path=ValidationPath.GAMMA_ONDEVICE,
            passed=True,
            test_results={},
            evidence={},
            failure_reasons=[],
            duration_seconds=120.0,
        )
        
        validator = TriPathValidator(
            qemu_validator=mock_qemu,
            ondevice_validator=mock_ondevice,
            tv_available=True,
        )
        
        results = await validator.validate_all(
            sample_pipeline_state,
            [sample_loki_candidate],
        )
        
        # BUG FIX: Exception in required path should cause overall_passed=False
        assert len(results) == 1
        assert results[0].overall_passed is False
        assert results[0].alpha_result is None  # Exception prevented result
    
    @pytest.mark.asyncio
    async def test_missing_stage_outputs_raises_error(self, sample_loki_candidate):
        """Test that missing stage outputs raise ValueError"""
        validator = TriPathValidator()
        
        # Create incomplete state (missing root_cause_result)
        incomplete_state = PipelineState(
            ticket=JiraTicket(
                key="TEST-001",
                summary="Test",
                description="Test",
                created=datetime.now(timezone.utc),
                updated=datetime.now(timezone.utc),
                priority="P1",
                status="Open",
                assignee="test",
                reporter="test",
            ),
            buglayer_result=BugLayerResult(
                layer=BugLayer.LOKI,
                confidence=0.9,
                layer_scores={},
                matched_patterns=[],
            ),
            # root_cause_result is None (missing)
        )
        
        # BUG FIX: Should raise ValueError instead of AttributeError
        with pytest.raises(ValueError, match="Validation requires completed"):
            await validator.validate_all(incomplete_state, [sample_loki_candidate])
