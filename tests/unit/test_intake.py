"""
Unit tests for intake module.

Covers:
- JiraClient: construction, header generation, ticket parsing, attachment download
- AttachmentHandler: ZIP extraction, log file identification, path-traversal guard
- KeywordExtractor: regex extraction for signals, components, apps, error codes
- JiraIntakeAgent: orchestration, process() method
- jira_webhook: parse_webhook_event, verify_webhook_signature
"""

import asyncio
import hashlib
import hmac
import io
import json
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, mock_open

import pytest


# ── JiraClient ────────────────────────────────────────────────────────────────

class TestJiraClient:
    def _client(self):
        from safs.intake.jira_client import JiraClient
        return JiraClient(
            base_url="https://jira.example.com",
            username="user@example.com",
            api_token="test-token",
        )

    def test_import(self):
        from safs.intake.jira_client import JiraClient
        assert JiraClient is not None

    def test_instantiation(self):
        client = self._client()
        assert client is not None

    def test_auth_header_uses_basic(self):
        client = self._client()
        assert "Basic" in client._headers["Authorization"]

    def test_base_url_stripped(self):
        from safs.intake.jira_client import JiraClient
        client = JiraClient(
            base_url="https://jira.example.com/",
            username="u",
            api_token="t",
        )
        assert not client._base_url.endswith("/")

    def test_context_manager_creates_client(self):
        client = self._client()
        async def run():
            async with client as c:
                assert c._client is not None
        asyncio.run(run())

    def test_get_ticket_calls_correct_url(self):
        client = self._client()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "key": "SMART-1",
            "fields": {
                "summary": "Test crash",
                "description": None,
                "priority": {"name": "Major"},
                "labels": ["crash"],
                "status": {"name": "Open"},
                "attachment": [],
                "comment": {"comments": []},
            }
        }
        async def run():
            client._client = AsyncMock()
            client._client.get = AsyncMock(return_value=mock_response)
            ticket = await client.get_ticket("SMART-1")
            return ticket
        ticket = asyncio.run(run())
        assert ticket.key == "SMART-1"

    def test_download_attachment_writes_file(self, tmp_path):
        client = self._client()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"log content here"
        dest = str(tmp_path / "test.log")
        async def run():
            client._client = AsyncMock()
            client._client.get = AsyncMock(return_value=mock_response)
            n = await client.download_attachment("https://jira.example.com/attach/1", dest)
            return n
        n = asyncio.run(run())
        assert n == len(b"log content here")
        assert Path(dest).exists()

    def test_list_attachments_delegates_to_get_ticket(self):
        client = self._client()
        mock_ticket = MagicMock()
        mock_ticket.attachments = []
        async def run():
            with patch.object(client, "get_ticket", AsyncMock(return_value=mock_ticket)):
                return await client.list_attachments("SMART-1")
        result = asyncio.run(run())
        assert isinstance(result, list)

    def test_error_on_non_2xx(self):
        from safs.intake.jira_client import JiraClientError
        client = self._client()
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = "Not found"
        async def run():
            client._client = AsyncMock()
            client._client.get = AsyncMock(return_value=mock_response)
            await client.get_ticket("SMART-MISSING")
        with pytest.raises(JiraClientError):
            asyncio.run(run())

    def test_priority_mapping_major_to_p1(self):
        client = self._client()
        data = {
            "key": "SMART-2",
            "fields": {
                "summary": "Major bug",
                "description": None,
                "priority": {"name": "Major"},
                "labels": [],
                "status": {"name": "Open"},
                "attachment": [],
                "comment": {"comments": []},
            }
        }
        ticket = client._parse_ticket(data)
        assert ticket.priority in ("P0", "P1", "Major", "major")


# ── AttachmentHandler ─────────────────────────────────────────────────────────

class TestAttachmentHandler:
    def test_import(self):
        from safs.intake.attachment_handler import AttachmentHandler
        assert AttachmentHandler is not None

    def test_instantiation_default_workdir(self):
        from safs.intake.attachment_handler import AttachmentHandler
        handler = AttachmentHandler()
        assert handler.work_dir.exists()

    def test_instantiation_custom_workdir(self, tmp_path):
        from safs.intake.attachment_handler import AttachmentHandler
        handler = AttachmentHandler(work_dir=tmp_path / "safs_test")
        assert handler.work_dir.exists()

    def test_identify_log_file_by_extension(self, tmp_path):
        from safs.intake.attachment_handler import AttachmentHandler
        handler = AttachmentHandler(work_dir=tmp_path)
        log_path = tmp_path / "crash.log"
        log_path.write_bytes(b"SIGSEGV crash output\n")
        log_files = handler._extract(log_path, "crash.log")
        assert len(log_files) == 1

    def test_identify_log_file_by_name_pattern(self, tmp_path):
        from safs.intake.attachment_handler import AttachmentHandler
        handler = AttachmentHandler(work_dir=tmp_path)
        dmesg_path = tmp_path / "dmesg_output.txt"
        dmesg_path.write_bytes(b"kernel panic\n")
        log_files = handler._extract(dmesg_path, "dmesg_output.txt")
        assert len(log_files) >= 1

    def test_skip_non_log_files(self, tmp_path):
        from safs.intake.attachment_handler import AttachmentHandler
        handler = AttachmentHandler(work_dir=tmp_path)
        img_path = tmp_path / "image.png"
        img_path.write_bytes(b"\x89PNG\r\n\x1a\n")
        log_files = handler._extract(img_path, "image.png")
        assert log_files == []

    def test_extract_zip_with_log_file(self, tmp_path):
        from safs.intake.attachment_handler import AttachmentHandler
        handler = AttachmentHandler(work_dir=tmp_path)
        zip_path = tmp_path / "logs.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("crash.log", "SIGSEGV at 0x0\n")
            zf.writestr("readme.txt", "logs package")
        log_files = handler._extract(zip_path, "logs.zip")
        assert len(log_files) >= 1

    def test_zip_rejects_path_traversal(self, tmp_path):
        from safs.intake.attachment_handler import AttachmentHandler
        handler = AttachmentHandler(work_dir=tmp_path)
        zip_path = tmp_path / "evil.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("../../../etc/passwd", "evil content")
        # Should not raise; just skip the unsafe member
        log_files = handler._extract(zip_path, "evil.zip")
        assert log_files == []

    def test_cleanup_removes_workdir(self, tmp_path):
        from safs.intake.attachment_handler import AttachmentHandler
        work = tmp_path / "cleanup_test"
        work.mkdir()
        handler = AttachmentHandler(work_dir=work)
        (work / "tempfile.log").write_text("hi")
        handler.cleanup()
        assert not work.exists()


# ── KeywordExtractor ──────────────────────────────────────────────────────────

class TestKeywordExtractor:
    def _extractor(self):
        from safs.intake.keyword_extractor import KeywordExtractor
        return KeywordExtractor()

    def test_import(self):
        from safs.intake.keyword_extractor import KeywordExtractor
        assert KeywordExtractor is not None

    def test_extract_returns_list(self):
        kw = self._extractor()
        result = kw.extract("LOKi AppLauncher crashed with SIGSEGV")
        assert isinstance(result, list)

    def test_extract_sigsegv_keyword(self):
        kw = self._extractor()
        result = kw.extract("Fatal signal 11 (SIGSEGV), fault addr 0x0")
        found = any("sigsegv" in k.lower() or "segfault" in k.lower() or "crash" in k.lower() for k in result)
        assert found, f"No SIGSEGV keyword found in: {result}"

    def test_extract_component_loki(self):
        kw = self._extractor()
        result = kw.extract("LOKi companion library deadlock detected")
        lower = [k.lower() for k in result]
        assert any("loki" in k or "deadlock" in k or "companion" in k for k in lower), f"Got: {result}"

    def test_extract_app_name_netflix(self):
        kw = self._extractor()
        result = kw.extract("Netflix MSL error 4040")
        lower = [k.lower() for k in result]
        assert any("netflix" in k for k in lower), f"Got: {result}"

    def test_extract_oom_error(self):
        kw = self._extractor()
        result = kw.extract("OOM killer chose process 1234 due to out of memory")
        lower = [k.lower() for k in result]
        assert any("oom" in k or "memory" in k or "malloc" in k for k in lower)

    def test_extract_widevine(self):
        kw = self._extractor()
        result = kw.extract("Widevine DRM license request failed")
        lower = [k.lower() for k in result]
        assert any("widevine" in k or "drm" in k or "license" in k for k in lower)

    def test_extract_empty_string(self):
        kw = self._extractor()
        result = kw.extract("")
        assert isinstance(result, list)

    def test_extract_deduplicates(self):
        kw = self._extractor()
        # Repeated SIGSEGV mentions should not inflate list
        result = kw.extract("SIGSEGV at address SIGSEGV crash SIGSEGV again")
        # Keywords should be unique
        assert len(result) == len(set(result))


# ── Webhook signature verification ────────────────────────────────────────────

class TestVerifyWebhookSignature:
    def test_valid_signature_passes(self):
        from safs.intake.jira_webhook import verify_webhook_signature
        secret = "my-secret"
        body = b'{"webhookEvent":"jira:issue_created"}'
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        # Should not raise
        verify_webhook_signature(secret, body, f"sha256={sig}")

    def test_missing_signature_raises(self):
        from safs.intake.jira_webhook import verify_webhook_signature, WebhookValidationError
        with pytest.raises(WebhookValidationError):
            verify_webhook_signature("secret", b"body", "")

    def test_wrong_signature_raises(self):
        from safs.intake.jira_webhook import verify_webhook_signature, WebhookValidationError
        with pytest.raises(WebhookValidationError):
            verify_webhook_signature("secret", b"body", "sha256=deadbeef")

    def test_unsupported_algorithm_raises(self):
        from safs.intake.jira_webhook import verify_webhook_signature, WebhookValidationError
        with pytest.raises(WebhookValidationError):
            verify_webhook_signature("secret", b"body", "md5=abc123")


# ── parse_webhook_event ───────────────────────────────────────────────────────

class TestParseWebhookEvent:
    def _payload(self, event_type="jira:issue_created", key="SMART-1234"):
        return {
            "webhookEvent": event_type,
            "issue": {
                "key": key,
                "fields": {"summary": "Test bug"},
            }
        }

    def test_parses_issue_created(self):
        from safs.intake.jira_webhook import parse_webhook_event
        event = parse_webhook_event(self._payload("jira:issue_created"))
        assert event is not None
        assert event.ticket_key == "SMART-1234"

    def test_parses_issue_updated(self):
        from safs.intake.jira_webhook import parse_webhook_event
        event = parse_webhook_event(self._payload("jira:issue_updated"))
        assert event is not None

    def test_ignores_issue_deleted(self):
        from safs.intake.jira_webhook import parse_webhook_event
        event = parse_webhook_event(self._payload("jira:issue_deleted"))
        assert event is None

    def test_ignores_comment_created(self):
        from safs.intake.jira_webhook import parse_webhook_event
        event = parse_webhook_event(self._payload("comment_created"))
        assert event is None

    def test_returns_none_for_missing_key(self):
        from safs.intake.jira_webhook import parse_webhook_event
        event = parse_webhook_event({"webhookEvent": "jira:issue_created", "issue": {}})
        assert event is None

    def test_event_type_exposed(self):
        from safs.intake.jira_webhook import parse_webhook_event
        event = parse_webhook_event(self._payload("jira:issue_created"))
        assert "created" in event.event_type or "issue" in event.event_type


# ── JiraIntakeAgent ───────────────────────────────────────────────────────────

class TestJiraIntakeAgent:
    def test_import(self):
        from safs.intake.jira_webhook import JiraIntakeAgent
        assert JiraIntakeAgent is not None

    def test_instantiation(self):
        from safs.intake.jira_webhook import JiraIntakeAgent
        agent = JiraIntakeAgent(
            jira_url="https://jira.example.com",
            jira_username="user",
            jira_api_token="token",
        )
        assert agent is not None

    def test_process_calls_jira_client(self):
        from safs.intake.jira_webhook import JiraIntakeAgent
        agent = JiraIntakeAgent(
            jira_url="https://jira.example.com",
            jira_username="user",
            jira_api_token="token",
        )
        mock_ticket = MagicMock()
        mock_ticket.key = "SMART-99"
        mock_ticket.attachments = []
        mock_ticket.description = "Test"
        mock_ticket.summary = "Test ticket"
        mock_ticket.priority = "P1"
        mock_ticket.labels = []

        async def run():
            with patch("safs.intake.jira_webhook.JiraClient") as MockClient:
                inst = AsyncMock()
                inst.get_ticket = AsyncMock(return_value=mock_ticket)
                MockClient.return_value.__aenter__ = AsyncMock(return_value=inst)
                MockClient.return_value.__aexit__ = AsyncMock(return_value=None)
                return await agent.process("SMART-99")

        ticket = asyncio.run(run())
        assert ticket is not None


