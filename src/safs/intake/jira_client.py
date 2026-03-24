"""
SAFS v6.0 — Jira REST API Client

Async client for fetching Jira ticket data, comments, and attachment metadata.
Authenticates via HTTP Basic (API token) as per Atlassian cloud documentation.

Usage:
    client = JiraClient(base_url=config.jira_url,
                        username=config.jira_username,
                        api_token=config.jira_api_token)
    ticket = await client.get_ticket("SMARTCAST-12345")
    attachments = await client.list_attachments("SMARTCAST-12345")
"""

from __future__ import annotations

import logging
from base64 import b64encode
from typing import Any, Optional
from urllib.parse import urljoin

import httpx

from safs.log_analysis.models import Attachment, JiraTicket

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
_MAX_RESULTS = 50  # Jira API default page size


class JiraClientError(Exception):
    """Raised on non-2xx responses from Jira REST API."""


class JiraClient:
    """
    Async Jira REST API v3 client.

    Supports:
    - Fetching ticket metadata (summary, description, priority, labels)
    - Listing attachment metadata
    - Downloading attachment binary content
    """

    def __init__(
        self,
        base_url: str,
        username: str,
        api_token: str,
        verify_ssl: bool = True,
    ) -> None:
        """
        Args:
            base_url: Jira instance base URL (e.g., https://vizio.atlassian.net)
            username: Jira account email
            api_token: Jira API token (from https://id.atlassian.com)
            verify_ssl: Whether to verify SSL certificate (default True)
        """
        self._base_url = base_url.rstrip("/")
        credentials = b64encode(f"{username}:{api_token}".encode()).decode()
        self._headers = {
            "Authorization": f"Basic {credentials}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        self._verify_ssl = verify_ssl
        self._client: Optional[httpx.AsyncClient] = None

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "JiraClient":
        self._client = httpx.AsyncClient(
            headers=self._headers,
            timeout=_TIMEOUT,
            verify=self._verify_ssl,
        )
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_ticket(self, ticket_key: str) -> JiraTicket:
        """
        Fetch full ticket metadata and attachments.

        Args:
            ticket_key: Jira issue key (e.g. "SMARTCAST-12345")

        Returns:
            JiraTicket Pydantic model populated from Jira API response.

        Raises:
            JiraClientError: on non-2xx response
        """
        url = f"{self._base_url}/rest/api/3/issue/{ticket_key}"
        params = {
            "fields": "summary,description,priority,labels,status,attachment,comment",
        }
        data = await self._get(url, params=params)
        return self._parse_ticket(data)

    async def list_attachments(self, ticket_key: str) -> list[Attachment]:
        """
        List attachments for a ticket (metadata only, no download).

        Args:
            ticket_key: Jira issue key

        Returns:
            List of Attachment models with metadata (no local file path yet)
        """
        ticket = await self.get_ticket(ticket_key)
        return ticket.attachments

    async def download_attachment(
        self, content_url: str, dest_path: str
    ) -> int:
        """
        Download attachment binary to a local file.

        Args:
            content_url: Jira attachment content URL
            dest_path: Absolute local file path to write to

        Returns:
            Number of bytes written
        """
        response = await self._raw_get(content_url)
        data = response.content
        with open(dest_path, "wb") as fh:
            fh.write(data)
        logger.debug("Downloaded %d bytes → %s", len(data), dest_path)
        return len(data)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get(self, url: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """Execute GET request and return parsed JSON."""
        client = self._require_client()
        response = await client.get(url, params=params)
        if response.status_code >= 400:
            raise JiraClientError(
                f"Jira API {response.status_code} for {url}: {response.text[:500]}"
            )
        return response.json()

    async def _raw_get(self, url: str) -> httpx.Response:
        """Execute raw GET (no JSON parsing)."""
        client = self._require_client()
        response = await client.get(url)
        if response.status_code >= 400:
            raise JiraClientError(
                f"Jira download {response.status_code} for {url}: {response.text[:200]}"
            )
        return response

    def _require_client(self) -> httpx.AsyncClient:
        if self._client is None:
            # Allow usage without context manager (creates ephemeral client)
            self._client = httpx.AsyncClient(
                headers=self._headers,
                timeout=_TIMEOUT,
                verify=self._verify_ssl,
            )
        return self._client

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    def _parse_ticket(self, data: dict[str, Any]) -> JiraTicket:
        """Convert raw Jira API response to JiraTicket model."""
        fields = data.get("fields", {})

        key = data.get("key", "UNKNOWN")
        summary = fields.get("summary", "")

        # Description: Jira API v3 returns ADF (Atlassian Document Format) JSON
        description_adf = fields.get("description")
        description = self._extract_text_from_adf(description_adf)

        # Priority
        priority_obj = fields.get("priority") or {}
        priority = priority_obj.get("name", "")
        # Map Jira names to SAFS P0-P4 format
        priority_map = {
            "Blocker": "P0",
            "Critical": "P1",
            "Major": "P1",
            "High": "P1",
            "Medium": "P2",
            "Normal": "P2",
            "Low": "P3",
            "Trivial": "P4",
            "Minor": "P3",
        }
        safs_priority = priority_map.get(priority, "P2")

        # Labels
        labels: list[str] = fields.get("labels", [])

        # Attachments
        raw_attachments = fields.get("attachment", [])
        attachments = [self._parse_attachment(a) for a in raw_attachments]

        # Extract streaming app from labels / summary
        streaming_app = self._detect_streaming_app(summary, description, labels)

        return JiraTicket(
            key=key,
            summary=summary,
            description=description,
            priority=safs_priority,
            attachments=attachments,
            streaming_app=streaming_app,
        )

    def _parse_attachment(self, raw: dict[str, Any]) -> Attachment:
        """Convert Jira attachment JSON to Attachment model."""
        return Attachment(
            id=str(raw.get("id", "")),
            filename=raw.get("filename", ""),
            size=raw.get("size", 0),
            mime_type=raw.get("mimeType", "application/octet-stream"),
            content_url=raw.get("content", ""),
        )

    @staticmethod
    def _extract_text_from_adf(adf: Any) -> str:
        """
        Recursively extract plain text from Atlassian Document Format (ADF) JSON.
        Falls back to str() if not ADF.
        """
        if adf is None:
            return ""
        if isinstance(adf, str):
            return adf

        texts: list[str] = []

        def _walk(node: Any) -> None:
            if isinstance(node, dict):
                if node.get("type") == "text":
                    texts.append(node.get("text", ""))
                for child in node.get("content", []):
                    _walk(child)
            elif isinstance(node, list):
                for item in node:
                    _walk(item)

        _walk(adf)
        return " ".join(texts).strip()

    @staticmethod
    def _detect_streaming_app(
        summary: str, description: str, labels: list[str]
    ) -> Optional[str]:
        """Detect affected streaming app from ticket content."""
        import re

        text = f"{summary} {description} {' '.join(labels)}".lower()
        apps = [
            ("Netflix", r"netflix"),
            ("Hulu", r"hulu"),
            ("Amazon", r"amazon|prime.video"),
            ("YouTube", r"youtube"),
            ("WatchFree", r"watchfree|watch.free"),
            ("Peacock", r"peacock"),
            ("Disney+", r"disney"),
            ("Max", r"\bmax\b|hbo.max"),
            ("Paramount+", r"paramount"),
            ("Pluto", r"pluto.tv"),
        ]
        for app_name, pattern in apps:
            if re.search(pattern, text):
                return app_name
        return None
