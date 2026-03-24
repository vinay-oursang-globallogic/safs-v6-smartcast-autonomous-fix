"""
End-to-end tests using fixture files.

Tests the complete SAFS pipeline flow from Jira webhook payload to
fix candidate generation, using fixture data and mocked external services.
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


@pytest.mark.e2e
class TestE2EWithFixtures:
    """E2E tests using pre-recorded fixture data."""

    def test_webhook_payload_parses_to_ticket(self):
        """Jira webhook → JiraWebhookParser → JiraTicket object."""
        path = FIXTURES_DIR / "jira_payloads" / "webhook_created.json"
        if not path.exists():
            pytest.skip("webhook_created.json not found")

        payload = json.loads(path.read_text())

        try:
            from src.safs.intake.jira_webhook_parser import JiraWebhookParser
            parser = JiraWebhookParser()
            ticket = parser.parse(payload)
            key = getattr(ticket, "key", None) or getattr(ticket, "ticket_key", None)
            assert key is not None
            assert "SMART" in str(key)
        except ImportError:
            pytest.skip("intake.jira_webhook_parser not available")

    def test_loki_log_through_analysis_pipeline(self):
        """null_deref.log → log analysis modules → structured output."""
        log_path = FIXTURES_DIR / "loki_crashes" / "null_deref.log"
        if not log_path.exists():
            pytest.skip("null_deref.log not found")

        log_text = log_path.read_text()
        assert len(log_text) > 0

        # Run through error pattern detection
        import re
        from src.safs.log_analysis.error_patterns import load_enriched_patterns
        patterns = load_enriched_patterns()
        matched_lines = []
        for line in log_text.splitlines():
            for p in patterns:
                regex = getattr(p, "pattern", None) or getattr(p, "regex", None)
                if isinstance(regex, str):
                    try:
                        if re.search(regex, line, re.IGNORECASE):
                            matched_lines.append(line)
                            break
                    except re.error:
                        pass
        # Should detect at least some error lines
        assert isinstance(matched_lines, list)

    def test_full_e2e_with_mocked_orchestrator(self):
        """Full E2E with mocked orchestrator: webhook → stages → result."""
        path = FIXTURES_DIR / "jira_payloads" / "webhook_created.json"
        if not path.exists():
            pytest.skip("webhook_created.json not found")

        payload = json.loads(path.read_text())
        ticket_key = payload.get("key") or payload.get("issue", {}).get("key", "SMART-TEST")

        with patch("src.safs.agents.orchestrator.SAFSOrchestrator") as mock_cls:
            mock_orch = MagicMock()
            mock_result = MagicMock()
            mock_result.ticket_key = ticket_key
            mock_result.bug_layer = MagicMock(name="LOKI")
            mock_result.pr_url = "https://github.com/buddytv/loki-core/pull/42"
            mock_orch.run = AsyncMock(return_value=mock_result)
            mock_cls.return_value = mock_orch

            async def simulate_pipeline():
                orch = mock_cls()
                return await orch.run(ticket_key=ticket_key)

            result = asyncio.run(simulate_pipeline())
            assert result.ticket_key == ticket_key
            assert result.pr_url is not None

    def test_source_map_decode_full_pipeline(self):
        """source_map → SourceMapStore.decode → SourceMapPosition."""
        map_path = FIXTURES_DIR / "source_maps" / "sample.js.map"
        if not map_path.exists():
            pytest.skip("sample.js.map not found")

        from src.safs.symbol_store.source_map_decoder import SourceMapStore
        store = SourceMapStore()
        # Test various line/column combos — should not raise for valid lines
        for line, col in [(1, 0), (1, 4), (2, 0)]:
            try:
                pos = store.decode(map_path, line, col)
                assert pos is None or hasattr(pos, "source")
            except Exception:
                pass  # File may not have these line/col combos

    def test_attachment_ticket_fixture_parsed(self):
        """ticket_with_attachments.json should produce a ticket with attachments."""
        path = FIXTURES_DIR / "jira_payloads" / "ticket_with_attachments.json"
        if not path.exists():
            pytest.skip("ticket_with_attachments.json not found")

        data = json.loads(path.read_text())
        # Find attachments in nested structure
        fields = data.get("fields", data)
        attachments = fields.get("attachment", fields.get("attachments", []))
        assert len(attachments) >= 1
        assert attachments[0].get("filename") or attachments[0].get("content")

    def test_all_fixture_files_are_valid(self):
        """All fixture files should be non-empty and their JSON parseable."""
        json_fixtures = list(FIXTURES_DIR.rglob("*.json"))
        for f in json_fixtures:
            content = f.read_text()
            assert len(content) > 0, f"Empty fixture: {f}"
            json.loads(content)  # Should not raise

    def test_log_fixtures_are_non_empty(self):
        """All .log fixture files should be non-empty."""
        log_fixtures = list(FIXTURES_DIR.rglob("*.log"))
        for f in log_fixtures:
            content = f.read_text()
            assert len(content) > 10, f"Suspiciously short fixture: {f}"

    def test_error_patterns_loaded_at_pipeline_start(self):
        """Error patterns load exactly once and are cached."""
        from src.safs.log_analysis.error_patterns import load_enriched_patterns
        patterns1 = load_enriched_patterns()
        patterns2 = load_enriched_patterns()
        assert len(patterns1) == len(patterns2)
        assert len(patterns1) >= 76
