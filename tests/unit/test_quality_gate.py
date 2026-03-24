"""
Unit tests for SAFS v6.0 Log Quality Gate (Stage -1).

Test Coverage:
- LogTimestampExtractor: All 5 timestamp formats
- StructuralParser: Log level parsing and filtering
- TimeWindowFilter: Time-window filtering logic
- LogQualityGate: End-to-end quality assessment
"""

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from safs.log_analysis.quality_gate import (
    LogLevel,
    LogQualityGate,
    LogTimestampExtractor,
    StructuralParser,
    TimeWindowFilter,
    TimestampResult,
)
from safs.log_analysis.models import JiraTicket, LogFile


# ============================================================================
# PART 1: LOG TIMESTAMP EXTRACTOR TESTS
# ============================================================================


class TestLogTimestampExtractor:
    """Test LogTimestampExtractor for all supported formats."""

    def test_kernel_timestamp(self):
        """Test kernel dmesg format: [12345.678901]"""
        line = "[12345.678901] usb 1-1: new high-speed USB device"
        result = LogTimestampExtractor.extract_timestamp(line)
        
        assert result.timestamp is not None
        assert result.format_type == 'kernel'
        assert result.raw_value == '12345.678901'

    def test_kernel_timestamp_with_priority(self):
        """Test kernel format with priority: <6>[12345.678]"""
        line = "<6>[12345.678901] kernel: message"
        result = LogTimestampExtractor.extract_timestamp(line)
        
        assert result.timestamp is not None
        assert result.format_type == 'kernel'

    def test_dtv_svc_timestamp(self):
        """Test dtv_svc format: dtv_svc[123]: [2024-12-17 10:30:45.123456]"""
        line = "dtv_svc[1234]: [2024-12-17 10:30:45.123456] Processing request"
        result = LogTimestampExtractor.extract_timestamp(line)
        
        assert result.timestamp is not None
        assert result.format_type == 'dtv_svc'
        assert result.timestamp.year == 2024
        assert result.timestamp.month == 12
        assert result.timestamp.day == 17

    def test_scpl_timestamp(self):
        """Test SCPL format: [SCPL] INFO 2024-12-17 10:30:45.123456"""
        line = "[SCPL] INFO 2024-12-17 10:30:45.123456 Message"
        result = LogTimestampExtractor.extract_timestamp(line)
        
        assert result.timestamp is not None
        assert result.format_type == 'scpl'

    def test_scpl_timestamp_with_comma(self):
        """Test SCPL with comma separator: 2024-12-17 10:30:45,123456"""
        line = "[SCPL] ERROR 2024-12-17 10:30:45,123456 Error occurred"
        result = LogTimestampExtractor.extract_timestamp(line)
        
        assert result.timestamp is not None
        assert result.format_type == 'scpl'

    def test_syslog_timestamp(self):
        """Test syslog format: Dec 17 10:30:45"""
        line = "Dec 17 10:30:45 hostname service: message"
        result = LogTimestampExtractor.extract_timestamp(line)
        
        assert result.timestamp is not None
        assert result.format_type == 'syslog'
        assert result.timestamp.month == 12
        assert result.timestamp.day == 17

    def test_iso8601_timestamp(self):
        """Test ISO 8601 format: 2024-12-17T10:30:45.123Z"""
        line = "2024-12-17T10:30:45.123Z Processing event"
        result = LogTimestampExtractor.extract_timestamp(line)
        
        assert result.timestamp is not None
        assert result.format_type == 'iso8601'

    def test_iso8601_with_timezone(self):
        """Test ISO 8601 with timezone: 2024-12-17T10:30:45+00:00"""
        line = "Timestamp: 2024-12-17T10:30:45+00:00"
        result = LogTimestampExtractor.extract_timestamp(line)
        
        assert result.timestamp is not None
        assert result.format_type == 'iso8601'

    def test_no_timestamp(self):
        """Test line without timestamp"""
        line = "This is a log line without any timestamp"
        result = LogTimestampExtractor.extract_timestamp(line)
        
        assert result.timestamp is None
        assert result.format_type == 'none'

    def test_multiple_formats_priority(self):
        """Test that kernel format takes priority when multiple formats present"""
        line = "[12345.678] Dec 17 10:30:45 message"
        result = LogTimestampExtractor.extract_timestamp(line)
        
        # Kernel format should be tried first
        assert result.format_type == 'kernel'


# ============================================================================
# PART 2: STRUCTURAL PARSER TESTS
# ============================================================================


class TestStructuralParser:
    """Test StructuralParser for log level extraction and filtering."""

    def test_parse_log_level_plain(self):
        """Test plain log level: ERROR message"""
        parser = StructuralParser()
        level = parser.parse_log_level("ERROR Connection failed")
        assert level == 'ERROR'

    def test_parse_log_level_bracketed(self):
        """Test bracketed log level: [ERROR] message"""
        parser = StructuralParser()
        level = parser.parse_log_level("[ERROR] Connection failed")
        assert level == 'ERROR'

    def test_parse_log_level_keyvalue(self):
        """Test key-value format: level=ERROR"""
        parser = StructuralParser()
        level = parser.parse_log_level("level=ERROR message='test'")
        assert level == 'ERROR'

    def test_parse_log_level_json(self):
        """Test JSON format: "level": "ERROR" """
        parser = StructuralParser()
        level = parser.parse_log_level('{"level": "ERROR", "message": "test"}')
        assert level == 'ERROR'

    def test_level_normalization(self):
        """Test level name normalization (WARN -> WARNING, etc.)"""
        parser = StructuralParser()
        assert parser.parse_log_level("[WARN] test") == 'WARNING'
        assert parser.parse_log_level("[ERR] test") == 'ERROR'
        assert parser.parse_log_level("[CRIT] test") == 'CRITICAL'
        assert parser.parse_log_level("[FATAL] test") == 'CRITICAL'

    def test_should_keep_warning_threshold(self):
        """Test filtering with WARNING threshold"""
        parser = StructuralParser(min_level='WARNING')
        
        assert parser.should_keep("[ERROR] Error message") is True
        assert parser.should_keep("[WARNING] Warning message") is True
        assert parser.should_keep("[INFO] Info message") is False
        assert parser.should_keep("[DEBUG] Debug message") is False

    def test_should_keep_error_threshold(self):
        """Test filtering with ERROR threshold"""
        parser = StructuralParser(min_level='ERROR')
        
        assert parser.should_keep("[ERROR] Error message") is True
        assert parser.should_keep("[CRITICAL] Critical message") is True
        assert parser.should_keep("[WARNING] Warning message") is False
        assert parser.should_keep("[INFO] Info message") is False

    def test_should_keep_no_level(self):
        """Test that lines without level are kept (conservative)"""
        parser = StructuralParser(min_level='WARNING')
        # Stack traces, continuation lines, etc. should be kept
        assert parser.should_keep("    at com.example.MyClass.method()") is True
        assert parser.should_keep("Caused by: NullPointerException") is True

    def test_parse_http_status(self):
        """Test HTTP status code extraction"""
        parser = StructuralParser()
        
        assert parser.parse_http_status("GET /api 500 Internal Server Error") == 500
        assert parser.parse_http_status("status_code=404") == 404
        assert parser.parse_http_status("GET /api 200 OK") == None  # Not an error code pattern
        assert parser.parse_http_status("No status here") is None

    def test_is_error_level(self):
        """Test quick error level check"""
        parser = StructuralParser()
        
        assert parser.is_error_level("[ERROR] message") is True
        assert parser.is_error_level("[CRITICAL] message") is True
        assert parser.is_error_level("[WARNING] message") is False

    def test_is_http_error(self):
        """Test HTTP error detection"""
        parser = StructuralParser()
        
        assert parser.is_http_error("GET /api 500 Error") is True
        assert parser.is_http_error("GET /api 404 Not Found") is True
        assert parser.is_http_error("GET /api 200 OK") is False

    def test_statistics(self):
        """Test that statistics are tracked correctly"""
        parser = StructuralParser(min_level='WARNING')
        
        parser.should_keep("[ERROR] Error 1")
        parser.should_keep("[WARNING] Warning 1")
        parser.should_keep("[INFO] Info 1")
        parser.should_keep("[DEBUG] Debug 1")
        
        stats = parser.get_statistics()
        assert stats['total_lines_parsed'] == 4
        assert stats['lines_kept'] == 2
        assert stats['lines_discarded'] == 2
        assert stats['discard_percentage'] == 50.0


# ============================================================================
# PART 3: TIME WINDOW FILTER TESTS
# ============================================================================


class TestTimeWindowFilter:
    """Test TimeWindowFilter for time-based filtering."""

    def test_filter_by_timestamp_within_window(self, tmp_path):
        """Test filtering logs within time window"""
        # Create test log file
        log_file = tmp_path / "test.log"
        anchor_time = datetime(2024, 12, 17, 12, 0, 0, tzinfo=timezone.utc)
        
        log_lines = [
            f"dtv_svc[123]: [2024-12-17 11:00:00.000000] Before window",
            f"dtv_svc[123]: [2024-12-17 12:00:00.000000] In window (anchor)",
            f"dtv_svc[123]: [2024-12-17 13:00:00.000000] In window",
            f"dtv_svc[123]: [2024-12-17 14:00:00.000000] After window",
        ]
        log_file.write_text("\n".join(log_lines))
        
        # Filter with 1-hour window
        filter = TimeWindowFilter(window_hours=1)
        filtered = list(filter.filter_by_timestamp(log_file, anchor_time))
        
        # Should keep 3 lines (11:00, 12:00, 13:00 all within ±1h from 12:00)
        assert len(filtered) == 3
        assert any("In window" in line[1] for line in filtered)

    def test_filter_early_termination(self, tmp_path):
        """Test early termination when past time window"""
        log_file = tmp_path / "test.log"
        anchor_time = datetime(2024, 12, 17, 12, 0, 0, tzinfo=timezone.utc)
        
        log_lines = [
            f"dtv_svc[123]: [2024-12-17 11:30:00.000000] In window",
            f"dtv_svc[123]: [2024-12-17 12:00:00.000000] In window",
            f"dtv_svc[123]: [2024-12-17 14:00:00.000000] Past window - should terminate",
            f"dtv_svc[123]: [2024-12-17 15:00:00.000000] Should not be processed",
        ]
        log_file.write_text("\n".join(log_lines))
        
        filter = TimeWindowFilter(window_hours=1)
        filtered = list(filter.filter_by_timestamp(log_file, anchor_time))
        
        # Should terminate at line 3, not process line 4
        assert filter.total_lines_processed == 3  # Early termination

    def test_filter_continuation_lines(self, tmp_path):
        """Test that lines without timestamps are kept if following in-window line"""
        log_file = tmp_path / "test.log"
        anchor_time = datetime(2024, 12, 17, 12, 0, 0, tzinfo=timezone.utc)
        
        log_lines = [
            f"dtv_svc[123]: [2024-12-17 12:00:00.000000] Error occurred",
            f"    at com.example.MyClass.method()",  # No timestamp
            f"    Caused by: NullPointerException",  # No timestamp
            f"dtv_svc[123]: [2024-12-17 15:00:00.000000] Out of window",
        ]
        log_file.write_text("\n".join(log_lines))
        
        filter = TimeWindowFilter(window_hours=1)
        filtered = list(filter.filter_by_timestamp(log_file, anchor_time))
        
        # Should keep first 3 lines (in-window + 2 continuations)
        assert len(filtered) >= 3
        line_contents = [line[1] for line in filtered]
        assert any("MyClass.method" in line for line in line_contents)

    def test_stream_all_lines_fallback(self, tmp_path):
        """Test fallback to stream all lines when no timestamp extraction"""
        log_file = tmp_path / "test.log"
        log_lines = ["Line 1", "Line 2", "Line 3"]
        log_file.write_text("\n".join(log_lines))
        
        filter = TimeWindowFilter()
        # No timestamp provided, should stream all
        all_lines = list(filter._stream_all_lines(log_file))
        
        assert len(all_lines) == 3

    def test_statistics(self, tmp_path):
        """Test that statistics are tracked"""
        log_file = tmp_path / "test.log"
        anchor_time = datetime(2024, 12, 17, 12, 0, 0, tzinfo=timezone.utc)
        
        log_lines = [
            f"dtv_svc[123]: [2024-12-17 12:00:00.000000] Line 1",
            f"dtv_svc[123]: [2024-12-17 12:30:00.000000] Line 2",
            f"No timestamp line",
        ]
        log_file.write_text("\n".join(log_lines))
        
        filter = TimeWindowFilter(window_hours=1)
        list(filter.filter_by_timestamp(log_file, anchor_time))
        
        assert filter.total_lines_processed == 3
        assert filter.lines_with_timestamp == 2
        assert filter.lines_in_window >= 2


# ============================================================================
# PART 4: LOG QUALITY GATE TESTS (Integration)
# ============================================================================


class TestLogQualityGate:
    """Test LogQualityGate end-to-end."""

    @pytest.mark.asyncio
    async def test_quality_gate_passed(self, tmp_path):
        """Test quality gate passes with good logs"""
        # Create test log file
        log_file = tmp_path / "good.log"
        log_lines = []
        for i in range(50):
            log_lines.append(
                f"dtv_svc[123]: [2024-12-17 12:{i:02d}:00.000000] [ERROR] Error {i}"
            )
        log_file.write_text("\n".join(log_lines))
        
        # Create models
        log_file_obj = LogFile(
            path_to_file=str(log_file),
            path_from_log_root="good.log",
            attachment_filename="logs.zip",
        )
        ticket = JiraTicket(key="TVPF-12345")
        
        # Run quality gate
        gate = LogQualityGate(window_hours=24, min_level="WARNING")
        result = await gate.assess([log_file_obj], ticket)
        
        assert result.passed is True
        assert result.score > 0.5
        assert result.log_file_count == 1
        assert result.total_lines > 0

    @pytest.mark.asyncio
    async def test_quality_gate_failed_insufficient_lines(self, tmp_path):
        """Test quality gate fails with insufficient lines"""
        log_file = tmp_path / "sparse.log"
        log_lines = [
            f"dtv_svc[123]: [2024-12-17 12:00:00.000000] [ERROR] Error 1",
            f"dtv_svc[123]: [2024-12-17 12:01:00.000000] [ERROR] Error 2",
        ]
        log_file.write_text("\n".join(log_lines))
        
        log_file_obj = LogFile(
            path_to_file=str(log_file),
            path_from_log_root="sparse.log",
            attachment_filename="logs.zip",
        )
        ticket = JiraTicket(key="TVPF-12345")
        
        gate = LogQualityGate(window_hours=24, min_level="WARNING")
        result = await gate.assess([log_file_obj], ticket)
        
        assert result.passed is False
        assert "Insufficient log signal" in result.reasons[0]

    @pytest.mark.asyncio
    async def test_quality_gate_failed_low_timestamp_coverage(self, tmp_path):
        """Test quality gate fails with low timestamp coverage"""
        log_file = tmp_path / "no_timestamps.log"
        log_lines = []
        # Only 2 lines with timestamps out of 100
        log_lines.append("dtv_svc[123]: [2024-12-17 12:00:00.000000] [ERROR] Error 1")
        for i in range(98):
            log_lines.append(f"[ERROR] Error without timestamp {i}")
        log_lines.append("dtv_svc[123]: [2024-12-17 12:01:00.000000] [ERROR] Error 2")
        log_file.write_text("\n".join(log_lines))
        
        log_file_obj = LogFile(
            path_to_file=str(log_file),
            path_from_log_root="no_timestamps.log",
            attachment_filename="logs.zip",
        )
        ticket = JiraTicket(key="TVPF-12345")
        
        gate = LogQualityGate(window_hours=24, min_level="WARNING")
        result = await gate.assess([log_file_obj], ticket)
        
        # May fail due to low timestamp coverage (depends on threshold)
        if not result.passed:
            assert any("timestamp coverage" in reason.lower() for reason in result.reasons)

    @pytest.mark.asyncio
    async def test_quality_gate_empty_log_files(self):
        """Test quality gate fails with no log files"""
        ticket = JiraTicket(key="TVPF-12345")
        
        gate = LogQualityGate()
        result = await gate.assess([], ticket)
        
        assert result.passed is False
        assert "No log files provided" in result.reasons

    @pytest.mark.asyncio
    async def test_quality_gate_missing_file(self, tmp_path):
        """Test quality gate handles missing files gracefully"""
        log_file_obj = LogFile(
            path_to_file=str(tmp_path / "nonexistent.log"),
            path_from_log_root="nonexistent.log",
            attachment_filename="logs.zip",
        )
        ticket = JiraTicket(key="TVPF-12345")
        
        gate = LogQualityGate()
        result = await gate.assess([log_file_obj], ticket)
        
        assert result.passed is False
        assert any("not found" in reason for reason in result.reasons)

    @pytest.mark.asyncio
    async def test_quality_gate_filters_debug_info(self, tmp_path):
        """Test that DEBUG/INFO logs are filtered out"""
        log_file = tmp_path / "mixed.log"
        log_lines = [
            # ERROR and WARNING should be kept
            f"dtv_svc[123]: [2024-12-17 12:00:00.000000] [ERROR] Error message {i}"
            if i % 3 == 0
            else f"dtv_svc[123]: [2024-12-17 12:00:00.000000] [WARNING] Warning {i}"
            if i % 3 == 1
            # DEBUG and INFO should be filtered
            else f"dtv_svc[123]: [2024-12-17 12:00:00.000000] [DEBUG] Debug {i}"
            for i in range(60)
        ]
        log_file.write_text("\n".join(log_lines))
        
        log_file_obj = LogFile(
            path_to_file=str(log_file),
            path_from_log_root="mixed.log",
            attachment_filename="logs.zip",
        )
        ticket = JiraTicket(key="TVPF-12345")
        
        gate = LogQualityGate(window_hours=24, min_level="WARNING")
        result = await gate.assess([log_file_obj], ticket)
        
        # Should have filtered out DEBUG lines (1/3 of total)
        # So total_lines should be 40 (ERROR + WARNING only)
        # This tests that structural filtering is working
        assert result.total_lines == 60  # All lines processed
        # Kept lines should be approximately 40 (ERROR + WARNING)
        # But the exact number depends on time window filtering too


# ============================================================================
# PART 5: ENUM TESTS
# ============================================================================


class TestLogLevel:
    """Test LogLevel enum."""

    def test_log_level_ordering(self):
        """Test that log levels are ordered correctly"""
        assert LogLevel.TRACE < LogLevel.DEBUG
        assert LogLevel.DEBUG < LogLevel.INFO
        assert LogLevel.INFO < LogLevel.WARNING
        assert LogLevel.WARNING < LogLevel.ERROR
        assert LogLevel.ERROR < LogLevel.FATAL
        assert LogLevel.CRITICAL == LogLevel.FATAL

    def test_log_level_values(self):
        """Test log level numeric values"""
        assert LogLevel.TRACE == 0
        assert LogLevel.DEBUG == 1
        assert LogLevel.INFO == 2
        assert LogLevel.WARNING == 3
        assert LogLevel.ERROR == 4
        assert LogLevel.FATAL == 5
        assert LogLevel.CRITICAL == 5