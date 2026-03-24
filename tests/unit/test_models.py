"""
Unit tests for SAFS v6.0 Pydantic Data Models.

Test Coverage:
- All enum values
- Model instantiation
- JSON serialization/deserialization
- Field validation
- Required vs optional fields
- Edge cases
"""

import json
import uuid
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from safs.log_analysis.models import (
    # Enums
    BugLayer,
    ErrorCategory,
    ConfidenceRouting,
    EventType,
    MistakeSeverity,
    FixStrategy,
    # Data Models
    LogLine,
    Event,
    Report,
    LogFile,
    Attachment,
    JiraTicket,
    # Pipeline Models
    QualityResult,
    BugLayerResult,
    LogAnalysisResult,
    RootCauseResult,
    ContextResult,
    ReproResult,
    ValidationResult,
    FixCandidate,
    PipelineState,
)
from safs.reproduction.models import ReproResultV2, ReproductionStatus, ReproductionStrategy


# ============================================================================
# ENUM TESTS
# ============================================================================


class TestBugLayer:
    """Test BugLayer enum."""

    def test_all_values(self):
        """Verify all enum values."""
        assert BugLayer.LOKI.value == "LOKI"
        assert BugLayer.HTML5.value == "HTML5"
        assert BugLayer.MEDIATEK.value == "MEDIATEK"
        assert BugLayer.CROSS_LAYER.value == "CROSS_LAYER"
        assert BugLayer.UNKNOWN.value == "UNKNOWN"

    def test_enum_count(self):
        """Verify we have exactly 5 layers."""
        assert len(BugLayer) == 5


class TestErrorCategory:
    """Test ErrorCategory enum."""

    def test_loki_categories(self):
        """Verify 8 LOKi categories."""
        loki_categories = [c for c in ErrorCategory if c.value.startswith("LOKI_")]
        assert len(loki_categories) == 8

    def test_html5_categories(self):
        """Verify HTML5/JS/EME categories."""
        html5_prefixes = ["COMPANION_", "JS_", "EME_", "KEYDOWN_", "FETCH_", 
                          "SHAKA_", "NETFLIX_", "AMAZON_", "HULU_", "WATCHFREE_",
                          "CHROMIUM_", "FOCUS_", "MEMORY_LEAK_"]
        html5_categories = [c for c in ErrorCategory 
                            if any(c.value.startswith(p) for p in html5_prefixes)]
        assert len(html5_categories) == 13

    def test_mtk_categories(self):
        """Verify 6 MediaTek categories."""
        mtk_categories = [c for c in ErrorCategory if c.value.startswith("MTK_")]
        assert len(mtk_categories) == 6

    def test_total_categories(self):
        """Verify total of 27 categories."""
        assert len(ErrorCategory) == 27


class TestConfidenceRouting:
    """Test ConfidenceRouting enum."""

    def test_all_values(self):
        """Verify all routing values."""
        assert ConfidenceRouting.AUTO_PR.value == "AUTO_PR"
        assert ConfidenceRouting.PR_WITH_REVIEW.value == "PR_WITH_REVIEW"
        assert ConfidenceRouting.ANALYSIS_ONLY.value == "ANALYSIS_ONLY"
        assert ConfidenceRouting.ESCALATE_HUMAN.value == "ESCALATE_HUMAN"


class TestEventType:
    """Test EventType enum."""

    def test_all_values(self):
        """Verify all event types."""
        assert EventType.LOGSTART.value == "LOGSTART"
        assert EventType.LOGEND.value == "LOGEND"
        assert EventType.SUSPEND.value == "SUSPEND"
        assert EventType.KEYPRESS.value == "KEYPRESS"
        assert EventType.ERIS.value == "ERIS"


class TestMistakeSeverity:
    """Test MistakeSeverity enum."""

    def test_all_values(self):
        """Verify all severity levels."""
        assert MistakeSeverity.CRITICAL.value == "CRITICAL"
        assert MistakeSeverity.HIGH.value == "HIGH"
        assert MistakeSeverity.MEDIUM.value == "MEDIUM"
        assert MistakeSeverity.LOW.value == "LOW"
        assert MistakeSeverity.INFO.value == "INFO"


class TestFixStrategy:
    """Test FixStrategy enum."""

    def test_all_values(self):
        """Verify all fix strategies."""
        assert FixStrategy.NULL_CHECK.value == "NULL_CHECK"
        assert FixStrategy.SMART_POINTER.value == "SMART_POINTER"
        assert FixStrategy.MUTEX_GUARD.value == "MUTEX_GUARD"
        assert FixStrategy.RETRY_WITH_BACKOFF.value == "RETRY_WITH_BACKOFF"
        assert FixStrategy.EVENT_LISTENER_CLEANUP.value == "EVENT_LISTENER_CLEANUP"
        assert FixStrategy.POLYFILL.value == "POLYFILL"
        assert FixStrategy.CONFIG_UPDATE.value == "CONFIG_UPDATE"
        assert FixStrategy.CROSS_LAYER_FIX.value == "CROSS_LAYER_FIX"
        assert FixStrategy.AUTO_ESCALATE.value == "AUTO_ESCALATE"
        assert FixStrategy.UNKNOWN.value == "UNKNOWN"


# ============================================================================
# DATA MODEL TESTS
# ============================================================================


class TestLogLine:
    """Test LogLine model."""

    def test_basic_instantiation(self):
        """Test basic LogLine creation."""
        line = LogLine(log_line="[2025-01-01 12:00:00] Test log line")
        assert line.log_line == "[2025-01-01 12:00:00] Test log line"
        assert line.log_prefix_timestamp is None

    def test_with_timestamps(self):
        """Test LogLine with timestamps."""
        now = datetime.now()
        line = LogLine(
            log_line="Test",
            log_prefix_timestamp=now,
            log_line_timestamp=now,
            wall_clock_timestamp=now,
        )
        assert line.log_prefix_timestamp == now
        assert line.log_line_timestamp == now
        assert line.wall_clock_timestamp == now

    def test_json_serialization(self):
        """Test JSON serialization."""
        line = LogLine(log_line="Test")
        json_str = line.model_dump_json()
        assert "Test" in json_str
        
        # Deserialize
        loaded = LogLine.model_validate_json(json_str)
        assert loaded.log_line == "Test"


class TestEvent:
    """Test Event model."""

    def test_basic_event(self):
        """Test basic Event creation."""
        log_line = LogLine(log_line="Key Press: BACK")
        event = Event(
            event_type=EventType.KEYPRESS,
            log_line=log_line,
        )
        assert event.event_type == EventType.KEYPRESS
        assert event.log_line.log_line == "Key Press: BACK"
        assert event.event_specific_data is None

    def test_event_with_data(self):
        """Test Event with specific data."""
        log_line = LogLine(log_line="ERIS: ABC-123456")
        event = Event(
            event_type=EventType.ERIS,
            log_line=log_line,
            event_specific_data={"code": "ABC-123456"},
        )
        assert event.event_specific_data["code"] == "ABC-123456"


class TestReport:
    """Test Report model."""

    def test_minimal_report(self):
        """Test minimal Report."""
        report = Report(
            analyzer="test_analyzer",
            title="Test Report",
            report="Test content",
        )
        assert report.analyzer == "test_analyzer"
        assert report.events == 0
        assert report.priority == 100

    def test_report_with_lines(self):
        """Test Report with log lines."""
        lines = [
            LogLine(log_line="Line 1"),
            LogLine(log_line="Line 2"),
        ]
        report = Report(
            analyzer="test",
            title="Test",
            report="Content",
            lines_data=lines,
            events=2,
        )
        assert len(report.lines_data) == 2


class TestLogFile:
    """Test LogFile model."""

    def test_basic_log_file(self):
        """Test basic LogFile."""
        log_file = LogFile(
            path_to_file="/tmp/log.txt",
            path_from_log_root="log.txt",
            attachment_filename="logs.zip",
        )
        assert log_file.path_to_file == "/tmp/log.txt"
        assert log_file.from_archive is False
        assert len(log_file.reports) == 0

    def test_log_file_with_reports(self):
        """Test LogFile with reports."""
        report = Report(analyzer="test", title="Test", report="Content")
        log_file = LogFile(
            path_to_file="/tmp/log.txt",
            path_from_log_root="log.txt",
            attachment_filename="logs.zip",
            reports=[report],
            from_archive=True,
        )
        assert log_file.from_archive is True
        assert len(log_file.reports) == 1


class TestAttachment:
    """Test Attachment model."""

    def test_basic_attachment(self):
        """Test basic Attachment."""
        attachment = Attachment(
            id="12345",
            filename="logs.zip",
            size=1024,
            mime_type="application/zip",
            content_url="https://jira.example.com/attachment/12345",
        )
        assert attachment.id == "12345"
        assert attachment.size == 1024
        assert attachment.already_processed is False

    def test_attachment_with_logs(self):
        """Test Attachment with log files."""
        log_file = LogFile(
            path_to_file="/tmp/log.txt",
            path_from_log_root="log.txt",
            attachment_filename="logs.zip",
        )
        attachment = Attachment(
            id="12345",
            filename="logs.zip",
            size=1024,
            mime_type="application/zip",
            content_url="https://jira.example.com/attachment/12345",
            log_files=[log_file],
            already_processed=True,
        )
        assert len(attachment.log_files) == 1
        assert attachment.already_processed is True


class TestJiraTicket:
    """Test JiraTicket model."""

    def test_minimal_ticket(self):
        """Test minimal Jira ticket."""
        ticket = JiraTicket(key="TVPF-12345")
        assert ticket.key == "TVPF-12345"
        assert ticket.summary == ""
        assert len(ticket.attachments) == 0
        assert len(ticket.analyzers_run) == 0

    def test_ticket_with_attachments(self):
        """Test ticket with attachments."""
        attachment = Attachment(
            id="1",
            filename="logs.zip",
            size=1024,
            mime_type="application/zip",
            content_url="http://example.com",
        )
        ticket = JiraTicket(
            key="TVPF-12345",
            summary="Test Bug",
            description="Bug description",
            attachments=[attachment],
            analyzers_run={"drain3", "tfidf"},
        )
        assert len(ticket.attachments) == 1
        assert "drain3" in ticket.analyzers_run


# ============================================================================
# PIPELINE RESULT MODEL TESTS
# ============================================================================


class TestQualityResult:
    """Test QualityResult model."""

    def test_passed_quality(self):
        """Test passed quality gate."""
        result = QualityResult(
            passed=True,
            score=0.95,
            log_file_count=5,
            total_lines=1000,
            timestamp_coverage=0.98,
        )
        assert result.passed is True
        assert result.score == 0.95

    def test_failed_quality(self):
        """Test failed quality gate."""
        result = QualityResult(
            passed=False,
            score=0.25,
            reasons=["Too few log lines", "No timestamps"],
        )
        assert result.passed is False
        assert len(result.reasons) == 2

    def test_score_validation(self):
        """Test score must be 0.0-1.0."""
        with pytest.raises(ValidationError):
            QualityResult(passed=True, score=1.5)


class TestBugLayerResult:
    """Test BugLayerResult model."""

    def test_basic_classification(self):
        """Test basic BugLayer classification."""
        result = BugLayerResult(
            layer=BugLayer.LOKI,
            confidence=0.92,
            layer_scores={BugLayer.LOKI: 0.92, BugLayer.HTML5: 0.08},
            matched_patterns=["LOKI_SEGFAULT_1", "LOKI_NULL_2"],
        )
        assert result.layer == BugLayer.LOKI
        assert result.confidence == 0.92
        assert len(result.matched_patterns) == 2

    def test_unknown_layer(self):
        """Test UNKNOWN layer classification."""
        result = BugLayerResult(
            layer=BugLayer.UNKNOWN,
            confidence=0.0,
        )
        assert result.layer == BugLayer.UNKNOWN
        assert result.confidence == 0.0


class TestLogAnalysisResult:
    """Test LogAnalysisResult model."""

    def test_empty_analysis(self):
        """Test empty analysis result."""
        result = LogAnalysisResult()
        assert len(result.correlations) == 0
        assert len(result.incidents) == 0
        assert len(result.anomalies) == 0

    def test_full_analysis(self):
        """Test full analysis with all fields."""
        result = LogAnalysisResult(
            correlations=[{"event1": "A", "event2": "B"}],
            incidents=[{"type": "crash", "count": 3}],
            anomalies=[{"anomaly": "spike"}],
            drain_clusters=[{"cluster_id": 1, "size": 100}],
            tfidf_keywords=["malloc", "SIGSEGV", "backtrace"],
            duplicate_groups=[["file1.log", "file2.log"]],
        )
        assert len(result.correlations) == 1
        assert len(result.tfidf_keywords) == 3
        assert len(result.duplicate_groups) == 1


class TestRootCauseResult:
    """Test RootCauseResult model."""

    def test_basic_rca(self):
        """Test basic RCA result."""
        result = RootCauseResult(
            root_cause="Null pointer dereference in AppLauncher::launch()",
            confidence=0.88,
            error_category=ErrorCategory.LOKI_SEGFAULT_NULL_DEREF,
            severity=MistakeSeverity.CRITICAL,
            affected_files=["src/app_launcher.cpp"],
        )
        assert result.confidence == 0.88
        assert result.error_category == ErrorCategory.LOKI_SEGFAULT_NULL_DEREF
        assert len(result.affected_files) == 1


class TestContextResult:
    """Test ContextResult model."""

    def test_empty_context(self):
        """Test empty context."""
        result = ContextResult()
        assert len(result.github_files) == 0
        assert result.registry_data is None

    def test_full_context(self):
        """Test full context with all sources."""
        result = ContextResult(
            github_files=[{"path": "src/app_launcher.cpp", "content": "..."}],
            code_index_results=[{"file": "app_launcher.cpp", "score": 0.95}],
            qdrant_results=[{"doc_id": "123", "score": 0.88}],
            registry_data={"vizio_ssh": "available"},
            context_summary="Context assembled from 3 sources",
        )
        assert len(result.github_files) == 1
        assert result.registry_data is not None


class TestReproResult:
    """Test ReproResult model."""

    def test_reproducible_bug(self):
        """Test reproducible bug."""
        result = ReproResult(
            reproducible=True,
            repro_steps=["Step 1", "Step 2", "Step 3"],
            repro_rate=0.90,
            repro_logs=["log1.txt", "log2.txt"],
        )
        assert result.reproducible is True
        assert len(result.repro_steps) == 3
        assert result.repro_rate == 0.90

    def test_non_reproducible(self):
        """Test non-reproducible bug."""
        result = ReproResult(
            reproducible=False,
            repro_rate=0.0,
        )
        assert result.reproducible is False


class TestValidationResult:
    """Test ValidationResult model."""

    def test_all_passed(self):
        """Test all validation paths passed."""
        result = ValidationResult(
            path_alpha_qemu={"status": "passed"},
            path_beta_playwright={"status": "passed"},
            path_gamma_ondevice={"status": "passed"},
            overall_passed=True,
        )
        assert result.overall_passed is True

    def test_with_failures(self):
        """Test validation with failures."""
        result = ValidationResult(
            path_alpha_qemu={"status": "failed"},
            path_beta_playwright={"status": "passed"},
            overall_passed=False,
            failure_reasons=["QEMU: ASan detected memory leak"],
        )
        assert result.overall_passed is False
        assert len(result.failure_reasons) == 1


class TestFixCandidate:
    """Test FixCandidate model."""

    def test_basic_fix(self):
        """Test basic fix candidate."""
        fix = FixCandidate(
            strategy=FixStrategy.NULL_CHECK,
            confidence=0.92,
            routing=ConfidenceRouting.AUTO_PR,
            diff="+ if (ptr != nullptr) { ... }",
            explanation="Added null check before dereference",
        )
        assert fix.strategy == FixStrategy.NULL_CHECK
        assert fix.confidence == 0.92
        assert fix.routing == ConfidenceRouting.AUTO_PR
        assert fix.fix_id is not None  # Auto-generated UUID

    def test_fix_with_validation(self):
        """Test fix with validation result."""
        validation = ValidationResult(
            overall_passed=True,
        )
        fix = FixCandidate(
            strategy=FixStrategy.SMART_POINTER,
            confidence=0.85,
            routing=ConfidenceRouting.PR_WITH_REVIEW,
            validation_result=validation,
        )
        assert fix.validation_result.overall_passed is True

    def test_fix_id_uniqueness(self):
        """Test fix_id is unique."""
        fix1 = FixCandidate(
            strategy=FixStrategy.NULL_CHECK,
            confidence=0.9,
            routing=ConfidenceRouting.AUTO_PR,
        )
        fix2 = FixCandidate(
            strategy=FixStrategy.NULL_CHECK,
            confidence=0.9,
            routing=ConfidenceRouting.AUTO_PR,
        )
        assert fix1.fix_id != fix2.fix_id


class TestPipelineState:
    """Test PipelineState model (master model)."""

    def test_minimal_state(self):
        """Test minimal pipeline state."""
        ticket = JiraTicket(key="TVPF-12345")
        state = PipelineState(ticket=ticket)
        
        assert state.ticket.key == "TVPF-12345"
        assert state.current_stage == "INIT"
        assert state.quality_result is None
        assert len(state.fix_candidates) == 0
        assert state.pipeline_id is not None

    def test_full_pipeline_state(self):
        """Test full pipeline state with all stages."""
        ticket = JiraTicket(key="TVPF-12345")
        quality = QualityResult(passed=True, score=0.95)
        buglayer = BugLayerResult(layer=BugLayer.LOKI, confidence=0.92)
        log_analysis = LogAnalysisResult()
        root_cause = RootCauseResult(
            root_cause="Null deref",
            confidence=0.88,
            error_category=ErrorCategory.LOKI_SEGFAULT_NULL_DEREF,
            severity=MistakeSeverity.CRITICAL,
        )
        context = ContextResult()
        repro = ReproResultV2(
            status=ReproductionStatus.REPRODUCED,
            strategy=ReproductionStrategy.DETERMINISTIC,
        )
        fix = FixCandidate(
            strategy=FixStrategy.NULL_CHECK,
            confidence=0.92,
            routing=ConfidenceRouting.AUTO_PR,
        )
        validation = ValidationResult(overall_passed=True)
        
        state = PipelineState(
            ticket=ticket,
            quality_result=quality,
            buglayer_result=buglayer,
            log_analysis_result=log_analysis,
            root_cause_result=root_cause,
            context_result=context,
            repro_result=repro,
            fix_candidates=[fix],
            validation_result=validation,
            pr_url="https://github.com/vizio/loki/pull/123",
            current_stage="COMPLETE",
        )
        
        assert state.quality_result.passed is True
        assert state.buglayer_result.layer == BugLayer.LOKI
        assert len(state.fix_candidates) == 1
        assert state.pr_url is not None

    def test_json_serialization(self):
        """Test pipeline state JSON serialization."""
        ticket = JiraTicket(key="TVPF-12345")
        state = PipelineState(ticket=ticket)
        
        json_str = state.model_dump_json()
        assert "TVPF-12345" in json_str
        
        # Deserialize
        loaded = PipelineState.model_validate_json(json_str)
        assert loaded.ticket.key == "TVPF-12345"
        assert loaded.pipeline_id == state.pipeline_id

    def test_pipeline_id_uniqueness(self):
        """Test pipeline_id is unique for each instance."""
        ticket1 = JiraTicket(key="TVPF-1")
        ticket2 = JiraTicket(key="TVPF-2")
        state1 = PipelineState(ticket=ticket1)
        state2 = PipelineState(ticket=ticket2)
        
        assert state1.pipeline_id != state2.pipeline_id

    def test_error_tracking(self):
        """Test error tracking in pipeline state."""
        ticket = JiraTicket(key="TVPF-12345")
        state = PipelineState(
            ticket=ticket,
            errors=["Stage 0 failed", "Timeout in Stage 3"],
        )
        assert len(state.errors) == 2


# ============================================================================
# INTEGRATION TESTS
# ============================================================================


class TestModelIntegration:
    """Test model integration and composition."""

    def test_full_pipeline_flow(self):
        """Test complete pipeline flow with all models."""
        # Create ticket with attachments
        log_file = LogFile(
            path_to_file="/tmp/loki.log",
            path_from_log_root="loki.log",
            attachment_filename="logs.zip",
        )
        attachment = Attachment(
            id="1",
            filename="logs.zip",
            size=1024,
            mime_type="application/zip",
            content_url="http://example.com",
            log_files=[log_file],
        )
        ticket = JiraTicket(
            key="TVPF-12345",
            summary="LOKi crash on app launch",
            attachments=[attachment],
        )
        
        # Create pipeline state
        state = PipelineState(ticket=ticket)
        
        # Stage -1: Quality Gate
        state.quality_result = QualityResult(
            passed=True,
            score=0.95,
            log_file_count=1,
            total_lines=1000,
            timestamp_coverage=0.98,
        )
        
        # Stage 0: BugLayerRouter
        state.buglayer_result = BugLayerResult(
            layer=BugLayer.LOKI,
            confidence=0.92,
            matched_patterns=["LOKI_SEGFAULT_1"],
        )
        
        # Stage 2: RCA
        state.root_cause_result = RootCauseResult(
            root_cause="Null pointer dereference",
            confidence=0.88,
            error_category=ErrorCategory.LOKI_SEGFAULT_NULL_DEREF,
            severity=MistakeSeverity.CRITICAL,
            affected_files=["src/app_launcher.cpp"],
        )
        
        # Stage 5: Fix Generation
        fix = FixCandidate(
            strategy=FixStrategy.NULL_CHECK,
            confidence=0.92,
            routing=ConfidenceRouting.AUTO_PR,
            diff="+ if (ptr != nullptr) {...}",
            explanation="Added null check",
        )
        state.fix_candidates = [fix]
        
        # Stage 6: Validation
        state.validation_result = ValidationResult(
            path_alpha_qemu={"status": "passed"},
            overall_passed=True,
        )
        
        # Stage 7: PR Created
        state.pr_url = "https://github.com/vizio/loki/pull/123"
        state.current_stage = "COMPLETE"
        state.completed_at = datetime.now(timezone.utc)
        
        # Verify complete flow
        assert state.ticket.key == "TVPF-12345"
        assert state.quality_result.passed is True
        assert state.buglayer_result.layer == BugLayer.LOKI
        assert state.root_cause_result.severity == MistakeSeverity.CRITICAL
        assert len(state.fix_candidates) == 1
        assert state.validation_result.overall_passed is True
        assert state.pr_url is not None
        
        # Test JSON round-trip
        json_str = state.model_dump_json()
        loaded_state = PipelineState.model_validate_json(json_str)
        assert loaded_state.ticket.key == state.ticket.key
        assert loaded_state.pr_url == state.pr_url
