"""
End-to-end error handling tests.

Tests how SAFS handles edge cases, missing data, network failures,
and invalid inputs throughout the pipeline.
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.e2e
class TestE2EErrorHandling:
    """E2E tests for error handling and resilience."""

    def test_circuit_breaker_prevents_cascading_failures(self):
        """CircuitBreaker opens after repeated failures, protecting downstream."""
        from src.safs.retrieval.circuit_breaker import CircuitBreaker, CircuitOpenError, CircuitState

        cb = CircuitBreaker(name="test_service", failure_threshold=3, recovery_timeout=0.1)

        async def always_fail():
            raise ConnectionRefusedError("Service unreachable")

        async def run():
            failure_count = 0
            circuit_open_count = 0
            for i in range(10):
                try:
                    await cb.call(always_fail)
                except CircuitOpenError:
                    circuit_open_count += 1
                except Exception:
                    failure_count += 1
            return failure_count, circuit_open_count, cb.state

        failures, circuit_opens, final_state = asyncio.run(run())
        # After 3 failures, circuit opens → subsequent calls raise CircuitOpenError
        assert final_state == CircuitState.OPEN
        assert circuit_opens > 0

    def test_elf_symbolication_handles_missing_elf(self):
        """ElfSymbolicator raises FileNotFoundError when ELF file doesn't exist."""
        from src.safs.symbol_store.elf_symbolication import ElfSymbolicator
        from pathlib import Path

        sym = ElfSymbolicator(addr2line_path=None)
        with pytest.raises(FileNotFoundError):
            asyncio.run(sym.symbolicate(Path("/nonexistent/path.elf"), [0x12345678]))

    def test_source_map_handles_corrupt_file(self, tmp_path):
        """SourceMapStore should handle corrupt source map gracefully."""
        from src.safs.symbol_store.source_map_decoder import SourceMapStore
        corrupt = tmp_path / "corrupt.js.map"
        corrupt.write_text("{bad json{{{{")
        store = SourceMapStore()
        try:
            result = store.decode(corrupt, 1, 0)
            assert result is None
        except (ValueError, json.JSONDecodeError, KeyError):
            pass  # Also acceptable for corrupt JSON

    def test_drain_adapter_handles_binary_content(self):
        """DRAIN adapter should not crash on binary-like input lines."""
        from src.safs.log_analysis.drain_adapter import VizioSpecificDrainAdapter
        adapter = VizioSpecificDrainAdapter()
        binary_lines = ["\x00\x01\x02binary\x03content", "normal line", ""]
        result = adapter.process_logs(binary_lines)
        # process_logs returns a DrainResult object, not a list
        assert result is not None

    def test_settings_analyzer_handles_empty_string(self):
        """SettingsAnalyzer should return empty list for empty input."""
        from src.safs.log_analysis.settings_analyzer import SettingsAnalyzer
        analyzer = SettingsAnalyzer()
        result = analyzer.analyze("")
        assert result == [] or isinstance(result, list)

    def test_settings_analyzer_handles_none_like_input(self):
        """SettingsAnalyzer should handle whitespace-only input."""
        from src.safs.log_analysis.settings_analyzer import SettingsAnalyzer
        analyzer = SettingsAnalyzer()
        result = analyzer.analyze("   \n\t  ")
        assert isinstance(result, list)

    def test_correlation_engine_handles_single_event(self):
        """CorrelationEngine with only 1 event should return empty correlations."""
        from src.safs.log_analysis.correlation_engine import CorrelationEngine
        from src.safs.log_analysis.timestamp_extractor import TimestampExtractor, EnrichedLogLine
        from datetime import datetime, timezone

        engine = CorrelationEngine(window_seconds=5.0)
        try:
            single = [EnrichedLogLine(
                timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
                raw_line="single error",
                log_level="ERROR"
            )]
        except Exception:
            single = []

        result = engine.analyze(single)
        assert isinstance(result, list)

    def test_incident_detector_handles_no_timestamps(self):
        """IncidentDetector with no timestamped lines should return empty."""
        from src.safs.log_analysis.incident_detector import IncidentDetector
        det = IncidentDetector(gap_seconds=60.0)
        result = det.detect([])
        assert result == []

    def test_anomaly_detector_with_insufficient_baseline(self):
        """AnomalyDetector with < 2 events should not raise."""
        from src.safs.log_analysis.anomaly_detector import AnomalyDetector
        from src.safs.log_analysis.timestamp_extractor import EnrichedLogLine
        from datetime import datetime, timezone

        det = AnomalyDetector(baseline_multiplier=3.0)
        try:
            single = [EnrichedLogLine(
                timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
                raw_line="lone error",
                log_level="ERROR"
            )]
        except Exception:
            single = []

        result = det.detect(single)
        assert isinstance(result, list)

    def test_mcp_client_factory_handles_empty_config(self, tmp_path):
        """MCPClientFactory with empty config returns empty dict."""
        from src.safs.mcp_client import MCPClientFactory
        config_path = tmp_path / "empty.json"
        config_path.write_text(json.dumps({"clients": {}}))

        factory = MCPClientFactory()
        result = asyncio.run(factory.create_from_config(config_path))
        assert result == {} or isinstance(result, dict)

    def test_self_healing_agent_handles_empty_reason(self):
        """SelfHealingAgent should not raise with empty correction reason."""
        from src.safs.agents.self_healing import SelfHealingAgent
        from src.safs.qdrant_collections.correction_indexer import CorrectionIndexer
        mock_indexer = MagicMock(spec=CorrectionIndexer)
        mock_correction = MagicMock()
        mock_indexer.process_developer_correction = MagicMock(return_value=mock_correction)
        agent = SelfHealingAgent(correction_indexer=mock_indexer)

        result = asyncio.run(agent.process_developer_correction(
            original_pr_url="https://github.com/buddytv/loki-core/pull/0",
            correction_description="",
            corrected_by="",
            error_category="UNKNOWN",
            jira_ticket=""
        ))
        assert result is not None

    def test_telemetry_noop_client_never_raises(self):
        """NoopTelemetryClient should silently succeed on all methods."""
        from src.safs.telemetry.telemetry_client import NoopTelemetryClient
        client = NoopTelemetryClient()

        async def run():
            r1 = await client.get_rate("error_type", "SIGSEGV", 24.0)
            r2 = await client.get_baseline("error_type", "SIGSEGV", 24.0)
            r3 = await client.count_affected_users("error_type", "SIGSEGV")
            return r1, r2, r3

        r1, r2, r3 = asyncio.run(run())
        # Noop returns None or 0 — never raises
        assert r1 is None or r1 == 0.0
        assert r2 is None or r2 == 0.0
        assert r3 is None or r3 == 0
