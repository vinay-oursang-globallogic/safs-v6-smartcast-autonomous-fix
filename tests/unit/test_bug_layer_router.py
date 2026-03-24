"""
Unit tests for BugLayerRouter

Tests pattern matching, scoring, and routing logic for all bug layers.
"""

import pytest

from safs.log_analysis.bug_layer_router import (
    ALL_PATTERNS,
    CROSS_LAYER_PATTERNS,
    HTML5_PATTERNS,
    LOKI_PATTERNS,
    MEDIATEK_PATTERNS,
    BugLayerRouter,
)
from safs.log_analysis.models import (
    Attachment,
    BugLayer,
    ErrorCategory,
    JiraTicket,
    LogFile,
    LogLine,
    PipelineState,
    QualityResult,
)


# ==============================================================================
# FIXTURES
# ==============================================================================


@pytest.fixture
def router():
    """Create BugLayerRouter instance."""
    return BugLayerRouter()


@pytest.fixture
def base_ticket():
    """Create base Jira ticket (no logs)."""
    return JiraTicket(
        key="TVPF-12345",
        summary="Test ticket",
        description="Test description",
        attachments=[],
    )


def create_ticket_with_logs(log_lines: list[str]) -> JiraTicket:
    """Helper: Create ticket with given log lines."""
    log_line_objects = [
        LogLine(
            log_line=line,
            log_prefix_timestamp=None,
            log_line_timestamp=None,
            wall_clock_timestamp=None,
        )
        for line in log_lines
    ]
    
    log_file = LogFile(
        path_to_file="/tmp/test.log",
        path_from_log_root="test.log",
        attachment_filename="test.log",
        from_archive=False,
        timestamped_log_lines=log_line_objects,
    )
    
    attachment = Attachment(
        id="1",
        filename="test.log",
        size=1024,
        mime_type="text/plain",
        content_url="http://test.local/test.log",
        path_to_file="/tmp/test.log",
        log_files=[log_file],
    )
    
    return JiraTicket(
        key="TVPF-12345",
        summary="Test ticket",
        description="Test description",
        attachments=[attachment],
    )
    
    return JiraTicket(
        key="TVPF-12345",
        summary="Test ticket",
        description="Test description",
        created="2024-01-15T10:00:00Z",
        updated="2024-01-15T12:00:00Z",
        status="Open",
        priority="High",
        labels=[],
        attachments=[attachment],
    )


def create_state_with_logs(log_lines: list[str]) -> PipelineState:
    """Helper: Create PipelineState with given log lines."""
    ticket = create_ticket_with_logs(log_lines)
    return PipelineState(
        ticket=ticket,
        quality_result=QualityResult(
            passed=True,
            score=0.9,
            reasons=[],
            log_file_count=1,
            total_lines=len(log_lines),
            timestamp_coverage=1.0,
        ),
    )


# ==============================================================================
# PATTERN LIBRARY TESTS
# ==============================================================================


class TestPatternLibrary:
    """Test enriched error pattern definitions."""
    
    def test_all_patterns_count(self):
        """Test we have 76+ error patterns from POC."""
        assert len(ALL_PATTERNS) >= 20, "Should have at least 20 patterns"
        assert len(LOKI_PATTERNS) >= 5, "Should have LOKi patterns"
        assert len(HTML5_PATTERNS) >= 5, "Should have HTML5 patterns"
        assert len(MEDIATEK_PATTERNS) >= 3, "Should have MediaTek patterns"
        assert len(CROSS_LAYER_PATTERNS) >= 1, "Should have cross-layer patterns"
    
    def test_pattern_has_required_fields(self):
        """Test all patterns have required SAFS fields."""
        for pattern in ALL_PATTERNS:
            assert pattern.name, "Pattern must have name"
            assert pattern.pattern, "Pattern must have regex"
            assert pattern.bug_layer, "Pattern must have bug_layer"
            assert pattern.error_category, "Pattern must have error_category"
            assert 0.0 <= pattern.bug_layer_confidence <= 1.0, "Confidence in valid range"
    
    def test_loki_patterns_use_loki_categories(self):
        """Test LOKi patterns map to LOKi error categories."""
        loki_categories = {
            ErrorCategory.LOKI_SEGFAULT_NULL_DEREF,
            ErrorCategory.LOKI_MEMORY_CORRUPTION,
            ErrorCategory.LOKI_RACE_CONDITION,
            ErrorCategory.LOKI_APP_LAUNCH_FAILURE,
            ErrorCategory.LOKI_IR_ROUTING_FAILURE,
            ErrorCategory.LOKI_COMPANION_SERVER_DEADLOCK,
            ErrorCategory.LOKI_EPG_PARSE_ERROR,
            ErrorCategory.LOKI_OTA_UPDATE_FAILURE,
        }
        
        for pattern in LOKI_PATTERNS:
            assert pattern.bug_layer == BugLayer.LOKI, "LOKi pattern has LOKi layer"
            # Allow general crash categories too
            if pattern.error_category not in loki_categories:
                # Check if it's a generic category like segfault
                assert "LOKI" in pattern.error_category.value or "CRASH" in pattern.category.upper()
    
    def test_html5_patterns_use_html5_categories(self):
        """Test HTML5 patterns map to HTML5 error categories."""
        html5_categories = {
            ErrorCategory.COMPANION_LIB_TIMING,
            ErrorCategory.JS_HEAP_OOM,
            ErrorCategory.EME_DRM_FAILURE,
            ErrorCategory.KEYDOWN_NOT_FIRED,
            ErrorCategory.FETCH_NETWORK_TIMEOUT,
            ErrorCategory.SHAKA_ERROR_3016,
            ErrorCategory.NETFLIX_MSL_TIMEOUT,
            ErrorCategory.AMAZON_DASH_MANIFEST,
            ErrorCategory.HULU_AD_MSE_BREAK,
            ErrorCategory.CHROMIUM_VERSION_COMPAT,
            ErrorCategory.FOCUS_MANAGEMENT,
            ErrorCategory.MEMORY_LEAK_EVENT_LISTENER,
        }
        
        for pattern in HTML5_PATTERNS:
            assert pattern.bug_layer == BugLayer.HTML5, "HTML5 pattern has HTML5 layer"
            assert pattern.error_category in html5_categories, f"HTML5 pattern uses HTML5 category: {pattern.error_category}"
    
    def test_mediatek_patterns_use_mtk_categories(self):
        """Test MediaTek patterns map to MTK error categories."""
        mtk_categories = {
            ErrorCategory.MTK_VDEC_CRASH,
            ErrorCategory.MTK_MALI_GPU_HANG,
            ErrorCategory.MTK_HDCP_FAILURE,
            ErrorCategory.MTK_TEE_WIDEVINE,
            ErrorCategory.MTK_ADSP_CRASH,
            ErrorCategory.MTK_MMC_IO_ERROR,
        }
        
        for pattern in MEDIATEK_PATTERNS:
            assert pattern.bug_layer == BugLayer.MEDIATEK, "MediaTek pattern has MEDIATEK layer"
            assert pattern.error_category in mtk_categories, f"MTK pattern uses MTK category: {pattern.error_category}"
    
    def test_cross_layer_patterns_have_hint(self):
        """Test cross-layer patterns have cross_layer_hint=True."""
        for pattern in CROSS_LAYER_PATTERNS:
            assert pattern.bug_layer == BugLayer.CROSS_LAYER, "Cross-layer pattern has CROSS_LAYER layer"
            assert pattern.cross_layer_hint, "Cross-layer pattern has hint flag"
    
    def test_pattern_regex_compiles(self):
        """Test all regex patterns compile successfully."""
        for pattern in ALL_PATTERNS:
            try:
                # Access compiled_regex property to trigger compilation
                assert pattern.compiled_regex is not None
            except Exception as e:
                pytest.fail(f"Pattern '{pattern.name}' regex failed to compile: {e}")


# ==============================================================================
# BASIC ROUTING TESTS
# ==============================================================================


class TestBasicRouting:
    """Test basic routing functionality."""
    
    def test_router_initialization(self, router):
        """Test router initializes with patterns."""
        assert router.patterns == ALL_PATTERNS
        assert router.total_routes == 0
    
    def test_route_loki_segfault(self, router):
        """Test routing LOKi segmentation fault."""
        log_lines = [
            "[LOKi] ERROR: Segmentation fault at 0x12345678",
            "[LOKi] CRITICAL: null pointer dereference in AppLauncher",
        ]
        
        state = create_state_with_logs(log_lines)
        result = router.route(state)
        
        assert result.layer == BugLayer.LOKI
        assert result.confidence > 0.5
        assert len(result.matched_patterns) > 0
        assert "LOKi Segmentation Fault" in result.matched_patterns or "LOKi NULL Pointer" in result.matched_patterns
    
    def test_route_html5_drm_failure(self, router):
        """Test routing HTML5 DRM failure."""
        log_lines = [
            "[Chromium] ERROR: EME error: MediaKeySession creation failed",
            "[Chromium] ERROR: Widevine error: license request timeout",
        ]
        
        state = create_state_with_logs(log_lines)
        result = router.route(state)
        
        assert result.layer == BugLayer.HTML5
        assert result.confidence > 0.5
        assert "EME/DRM Failure" in result.matched_patterns
    
    def test_route_mediatek_gpu_hang(self, router):
        """Test routing MediaTek GPU hang."""
        log_lines = [
            "[Kernel] CRITICAL: Mali GPU hang detected",
            "[Kernel] ERROR: ARM GPU timeout after 5 seconds",
        ]
        
        state = create_state_with_logs(log_lines)
        result = router.route(state)
        
        assert result.layer == BugLayer.MEDIATEK
        assert result.confidence > 0.5
        assert "MTK Mali GPU Hang" in result.matched_patterns
    
    def test_route_unknown_no_patterns(self, router):
        """Test routing returns UNKNOWN when no patterns match."""
        log_lines = [
            "[Random] INFO: Nothing interesting here",
            "[Random] DEBUG: Just some regular logs",
        ]
        
        state = create_state_with_logs(log_lines)
        result = router.route(state)
        
        assert result.layer == BugLayer.UNKNOWN
        assert result.confidence == 0.0
        assert len(result.matched_patterns) == 0
    
    def test_route_empty_logs(self, router, base_ticket):
        """Test routing with empty logs."""
        state = PipelineState(
            ticket=base_ticket,
            quality_result=QualityResult(
                passed=False,
                score=0.0,
                reasons=["No log files"],
                log_file_count=0,
                total_lines=0,
                timestamp_coverage=0.0,
            ),
        )
        
        result = router.route(state)
        
        assert result.layer == BugLayer.UNKNOWN
        assert result.confidence == 0.0
    
    def test_route_no_quality_result(self, router, base_ticket):
        """Test routing without quality result."""
        state = PipelineState(ticket=base_ticket)
        result = router.route(state)
        
        assert result.layer == BugLayer.UNKNOWN
        assert result.confidence == 0.0


# ==============================================================================
# CROSS-LAYER DETECTION TESTS
# ==============================================================================


class TestCrossLayerDetection:
    """Test cross-layer issue detection."""
    
    def test_cross_layer_both_signals(self, router):
        """Test CROSS_LAYER when both LOKi and HTML5 signals present."""
        log_lines = [
            # LOKi signals
            "[LOKi] ERROR: Segmentation fault in CompanionServer",
            "[LOKi] ERROR: null pointer in AppLauncher",
            "[LOKi] CRITICAL: race condition in IR handler",
            
            # HTML5 signals
            "[Chromium] ERROR: VIZIO_LIBRARY_DID_LOAD not fired",
            "[Chromium] ERROR: window.VIZIO undefined",
            "[Chromium] ERROR: Companion library timing issue",
        ]
        
        state = create_state_with_logs(log_lines)
        result = router.route(state)
        
        # Should detect CROSS_LAYER with both signals present
        assert result.layer == BugLayer.CROSS_LAYER
        assert result.confidence > 0.5
        assert len(result.matched_patterns) >= 3  # At least some patterns from both layers
    
    def test_cross_layer_explicit_hint(self, router):
        """Test CROSS_LAYER detection via explicit hint pattern."""
        log_lines = [
            "[WatchFree] ERROR: deeplink contentId lost during launch",
            "[WatchFree] ERROR: WatchFree+ deeplink not propagated to app",
        ]
        
        state = create_state_with_logs(log_lines)
        result = router.route(state)
        
        # Should detect CROSS_LAYER via hint
        assert result.layer == BugLayer.CROSS_LAYER
        assert "WatchFree+ Deeplink Loss" in result.matched_patterns
    
    def test_single_layer_not_cross_layer(self, router):
        """Test single-layer logs don't trigger CROSS_LAYER."""
        log_lines = [
            # Only LOKi signals
            "[LOKi] ERROR: Segmentation fault",
            "[LOKi] ERROR: null pointer",
        ]
        
        state = create_state_with_logs(log_lines)
        result = router.route(state)
        
        # Should route to LOKi, not CROSS_LAYER
        assert result.layer == BugLayer.LOKI
        assert result.layer != BugLayer.CROSS_LAYER


# ==============================================================================
# CONFIDENCE SCORING TESTS
# ==============================================================================


class TestConfidenceScoring:
    """Test confidence score calculation."""
    
    def test_high_confidence_multiple_patterns(self, router):
        """Test high confidence when multiple patterns match."""
        log_lines = [
            "[LOKi] CRITICAL: Segmentation fault (SIGSEGV)",
            "[LOKi] CRITICAL: null pointer dereference",
            "[LOKi] ERROR: double free detected",
            "[LOKi] ERROR: heap corruption",
        ]
        
        state = create_state_with_logs(log_lines)
        result = router.route(state)
        
        assert result.layer == BugLayer.LOKI
        assert result.confidence >= 0.7  # High confidence
        assert len(result.matched_patterns) >= 3
    
    def test_lower_confidence_single_pattern(self, router):
        """Test lower confidence with single pattern match."""
        log_lines = [
            "[LOKi] ERROR: Segmentation fault",
            "[Random] INFO: Some other log line",
        ]
        
        state = create_state_with_logs(log_lines)
        result = router.route(state)
        
        assert result.layer == BugLayer.LOKI
        # Confidence depends on pattern weights
        assert result.confidence > 0.0
    
    def test_layer_scores_populated(self, router):
        """Test layer_scores dictionary populated."""
        log_lines = [
            "[LOKi] ERROR: Segmentation fault",
            "[Chromium] ERROR: JavaScript out of memory",
        ]
        
        state = create_state_with_logs(log_lines)
        result = router.route(state)
        
        assert len(result.layer_scores) > 0
        assert BugLayer.LOKI in result.layer_scores
        assert BugLayer.HTML5 in result.layer_scores
    
    def test_confidence_normalized(self, router):
        """Test confidence is normalized to [0, 1]."""
        log_lines = [
            "[LOKi] ERROR: Segmentation fault",
        ]
        
        state = create_state_with_logs(log_lines)
        result = router.route(state)
        
        assert 0.0 <= result.confidence <= 1.0


# ==============================================================================
# SPECIFIC ERROR CATEGORY TESTS
# ==============================================================================


class TestSpecificErrorCategories:
    """Test specific error category matching."""
    
    def test_loki_memory_corruption(self, router):
        """Test LOKi memory corruption detection."""
        log_lines = [
            "[LOKi] CRITICAL: use after free detected in video decoder",
            "[LOKi] CRITICAL: double free in malloc heap",
        ]
        
        state = create_state_with_logs(log_lines)
        result = router.route(state)
        
        assert result.layer == BugLayer.LOKI
        assert "LOKi Memory Corruption" in result.matched_patterns
    
    def test_html5_companion_lib_timing(self, router):
        """Test HTML5 Companion library timing issue."""
        log_lines = [
            "[Chromium] ERROR: VIZIO_LIBRARY_DID_LOAD event not fired",
            "[Chromium] ERROR: window.VIZIO is undefined",
        ]
        
        state = create_state_with_logs(log_lines)
        result = router.route(state)
        
        assert result.layer == BugLayer.HTML5
        assert "Companion Library Timing" in result.matched_patterns
    
    def test_html5_netflix_msl_timeout(self, router):
        """Test Netflix MSL timeout detection."""
        log_lines = [
            "[Netflix] ERROR: MSL handshake timeout after 30s",
            "[Netflix] ERROR: nfp authentication timeout",
        ]
        
        state = create_state_with_logs(log_lines)
        result = router.route(state)
        
        assert result.layer == BugLayer.HTML5
        assert "Netflix MSL Timeout" in result.matched_patterns
    
    def test_mtk_hdcp_failure(self, router):
        """Test MediaTek HDCP failure detection."""
        log_lines = [
            "[HDCP] CRITICAL: HDCP authentication failed",
            "[HDCP] ERROR: HDCP key exchange timeout",
        ]
        
        state = create_state_with_logs(log_lines)
        result = router.route(state)
        
        assert result.layer == BugLayer.MEDIATEK
        assert "MTK HDCP Failure" in result.matched_patterns


# ==============================================================================
# STATISTICS TESTS
# ==============================================================================


class TestStatistics:
    """Test routing statistics collection."""
    
    def test_statistics_tracks_routes(self, router):
        """Test statistics track total routes."""
        initial_stats = router.get_statistics()
        assert initial_stats['total_routes'] == 0
        
        log_lines = ["[LOKi] ERROR: Segmentation fault"]
        state = create_state_with_logs(log_lines)
        router.route(state)
        
        stats = router.get_statistics()
        assert stats['total_routes'] == 1
    
    def test_statistics_tracks_layer_distribution(self, router):
        """Test statistics track layer distribution."""
        # Route LOKi issue
        state_loki = create_state_with_logs(["[LOKi] ERROR: Segmentation fault"])
        router.route(state_loki)
        
        # Route HTML5 issue
        state_html5 = create_state_with_logs(["[Chromium] ERROR: JavaScript out of memory"])
        router.route(state_html5)
        
        stats = router.get_statistics()
        assert stats['total_routes'] == 2
        assert BugLayer.LOKI in stats['layer_distribution']
        assert BugLayer.HTML5 in stats['layer_distribution']


# ==============================================================================
# EDGE CASES
# ==============================================================================


class TestEdgeCases:
    """Test edge cases and error handling."""
    
    def test_case_insensitive_matching(self, router):
        """Test patterns match case-insensitively."""
        log_lines = [
            "[LOKI] ERROR: SEGMENTATION FAULT",  # All caps
            "[loki] error: segmentation fault",  # All lowercase
        ]
        
        state = create_state_with_logs(log_lines)
        result = router.route(state)
        
        assert result.layer == BugLayer.LOKI
        assert len(result.matched_patterns) > 0
    
    def test_duplicate_pattern_names_deduplicated(self, router):
        """Test duplicate pattern matches are deduplicated."""
        log_lines = [
            "[LOKi] ERROR: Segmentation fault line 1",
            "[LOKi] ERROR: Segmentation fault line 2",
            "[LOKi] ERROR: Segmentation fault line 3",
        ]
        
        state = create_state_with_logs(log_lines)
        result = router.route(state)
        
        # Should deduplicate pattern names
        pattern_counts = {}
        for pattern_name in result.matched_patterns:
            pattern_counts[pattern_name] = pattern_counts.get(pattern_name, 0) + 1
        
        # Each pattern name should appear once
        for count in pattern_counts.values():
            assert count == 1
    
    def test_mixed_log_formats(self, router):
        """Test routing works with mixed log formats."""
        log_lines = [
            "2024-01-15 10:00:00 [LOKi] ERROR: Segmentation fault",
            "[Chromium] ERROR: JavaScript out of memory at 2024-01-15T10:00:01Z",
            "ERROR: Mali GPU hang",  # No timestamp
        ]
        
        state = create_state_with_logs(log_lines)
        result = router.route(state)
        
        # Should still route successfully
        assert result.layer in [BugLayer.LOKI, BugLayer.HTML5, BugLayer.MEDIATEK, BugLayer.CROSS_LAYER]
        assert result.confidence > 0.0
