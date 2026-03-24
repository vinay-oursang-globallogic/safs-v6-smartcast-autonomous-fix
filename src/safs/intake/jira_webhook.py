"""
SAFS v6.0 — Jira Webhook Handler + Intake Agent

Provides:
1. ``JiraWebhookHandler`` — FastAPI router that receives Jira webhook events,
   validates the HMAC-SHA256 signature, and enqueues tickets.
2. ``JiraIntakeAgent`` — Orchestrates the full intake pipeline:
   a. Fetch ticket from Jira REST API
   b. Download attachments
   c. Extract log files from archives
   d. Extract context keywords
   e. Return populated JiraTicket ready for the SAFS pipeline

Webhook signature verification:
  Jira computes ``HMAC-SHA256(secret, body)`` and sends it in
  ``X-Hub-Signature: sha256=<hex>``. We verify before processing.

Usage:
    # As a standalone FastAPI application:
    from safs.intake.jira_webhook import create_webhook_app
    app = create_webhook_app(config)

    # As intake agent only (no HTTP server):
    agent = JiraIntakeAgent(config)
    ticket = await agent.process("SMARTCAST-12345")
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import tempfile
from pathlib import Path
from typing import Any, Callable, Coroutine, Optional

from safs.log_analysis.models import JiraTicket

from .attachment_handler import AttachmentHandler
from .jira_client import JiraClient
from .keyword_extractor import KeywordExtractor

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Webhook models (lightweight, no FastAPI required at import time)
# ---------------------------------------------------------------------------


class WebhookEvent:
    """Parsed Jira webhook event."""

    def __init__(
        self,
        event_type: str,
        ticket_key: str,
        issue_data: dict[str, Any],
    ) -> None:
        self.event_type = event_type
        self.ticket_key = ticket_key
        self.issue_data = issue_data

    def __repr__(self) -> str:
        return f"WebhookEvent(type={self.event_type}, key={self.ticket_key})"


class WebhookValidationError(Exception):
    """Raised when webhook signature validation fails."""


# ---------------------------------------------------------------------------
# Webhook signature verification
# ---------------------------------------------------------------------------


def verify_webhook_signature(
    secret: str,
    body: bytes,
    signature_header: str,
) -> None:
    """
    Verify Jira webhook HMAC-SHA256 signature.

    Args:
        secret: Shared webhook secret configured in Jira
        body: Raw request body bytes
        signature_header: Value of ``X-Hub-Signature`` header

    Raises:
        WebhookValidationError: if signature is missing or invalid
    """
    if not signature_header:
        raise WebhookValidationError("Missing X-Hub-Signature header")

    parts = signature_header.split("=", 1)
    if len(parts) != 2 or parts[0] != "sha256":
        raise WebhookValidationError(
            f"Unsupported signature algorithm in header: {signature_header}"
        )

    expected_hex = parts[1]
    actual_hmac = hmac.new(
        secret.encode(), body, hashlib.sha256
    ).hexdigest()

    # Constant-time comparison to prevent timing attacks
    if not hmac.compare_digest(actual_hmac, expected_hex):
        raise WebhookValidationError("Webhook signature mismatch — request rejected")


def parse_webhook_event(payload: dict[str, Any]) -> Optional[WebhookEvent]:
    """
    Parse a raw Jira webhook payload.

    Returns None for event types we don't care about (e.g. comment created).
    Returns WebhookEvent for ``jira:issue_created`` and ``jira:issue_updated``.
    """
    event_type = payload.get("webhookEvent", "")
    issue = payload.get("issue", {})
    ticket_key = issue.get("key", "")

    if not ticket_key:
        logger.debug("Ignoring webhook event without issue key: %s", event_type)
        return None

    # Only process new bugs or updated bugs (not comments, transitions, etc.)
    relevant_events = {
        "jira:issue_created",
        "jira:issue_updated",
        "issue_created",
        "issue_updated",
    }
    if event_type not in relevant_events:
        logger.debug("Ignoring webhook event type: %s", event_type)
        return None

    return WebhookEvent(
        event_type=event_type,
        ticket_key=ticket_key,
        issue_data=issue,
    )


# ---------------------------------------------------------------------------
# JiraIntakeAgent — main intake orchestrator
# ---------------------------------------------------------------------------


class JiraIntakeAgent:
    """
    Orchestrates the full Jira intake pipeline.

    Given a Jira ticket key (from webhook or CLI), it:
    1. Fetches ticket metadata from Jira REST API
    2. Downloads each attachment
    3. Extracts log files from ZIP/tar archives
    4. Extracts technical context keywords
    5. Returns a fully populated JiraTicket
    """

    def __init__(
        self,
        jira_url: str,
        jira_username: str,
        jira_api_token: str,
        work_dir: Optional[Path] = None,
    ) -> None:
        """
        Args:
            jira_url: Jira instance base URL
            jira_username: Jira account email
            jira_api_token: Jira API token
            work_dir: Working directory for downloaded files
        """
        self._jira_url = jira_url
        self._jira_username = jira_username
        self._jira_api_token = jira_api_token
        self._work_dir = work_dir
        self._keyword_extractor = KeywordExtractor()

    async def process(self, ticket_key: str) -> JiraTicket:
        """
        Full intake pipeline for a single ticket.

        Args:
            ticket_key: Jira issue key (e.g. "SMARTCAST-12345")

        Returns:
            JiraTicket with all attachments downloaded and log files extracted
        """
        async with JiraClient(
            base_url=self._jira_url,
            username=self._jira_username,
            api_token=self._jira_api_token,
        ) as client:
            # Step 1: Fetch ticket metadata
            logger.info("Fetching Jira ticket %s", ticket_key)
            ticket = await client.get_ticket(ticket_key)

            # Step 2 & 3: Download + extract each attachment
            if ticket.attachments:
                work_dir = self._work_dir or Path(
                    tempfile.mkdtemp(prefix=f"safs_{ticket_key}_")
                )
                handler = AttachmentHandler(work_dir=work_dir)

                for attachment in ticket.attachments:
                    try:
                        log_files = await handler.process(attachment, client)
                        logger.info(
                            "Attachment %s → %d log files extracted",
                            attachment.filename,
                            len(log_files),
                        )
                    except Exception as exc:
                        logger.error(
                            "Failed to process attachment %s: %s",
                            attachment.filename,
                            exc,
                        )

            # Step 4: Extract context keywords
            keywords = self._keyword_extractor.extract_from_ticket(
                summary=ticket.summary,
                description=ticket.description,
            )
            logger.info(
                "Extracted %d context keywords for %s: %s",
                len(keywords),
                ticket_key,
                keywords[:10],
            )

            # Attach keywords to ticket description area for downstream use
            if keywords:
                ticket.description = (
                    ticket.description
                    + "\n\n[SAFS Context Keywords]: "
                    + ", ".join(keywords)
                )

        return ticket

    async def process_from_webhook(self, event: WebhookEvent) -> JiraTicket:
        """
        Process a ticket from a parsed webhook event.

        Args:
            event: Parsed WebhookEvent

        Returns:
            Populated JiraTicket
        """
        return await self.process(event.ticket_key)


# ---------------------------------------------------------------------------
# FastAPI webhook application factory (optional — only import if FastAPI available)
# ---------------------------------------------------------------------------


def create_webhook_app(
    intake_agent: "JiraIntakeAgent",
    webhook_secret: Optional[str] = None,
    on_ticket_ready: Optional[Callable[[JiraTicket], Coroutine[Any, Any, None]]] = None,
) -> Any:
    """
    Create a FastAPI application that receives Jira webhook events.

    Args:
        intake_agent: Configured JiraIntakeAgent
        webhook_secret: Optional HMAC-SHA256 secret for signature verification
        on_ticket_ready: Async callback invoked when a ticket is ready for processing

    Returns:
        FastAPI application instance

    Raises:
        ImportError: if FastAPI / uvicorn are not installed
    """
    try:
        from fastapi import FastAPI, HTTPException, Request, Response
    except ImportError as exc:
        raise ImportError(
            "FastAPI is required for the webhook server. "
            "Install with: pip install fastapi uvicorn"
        ) from exc

    app = FastAPI(title="SAFS Jira Webhook", version="6.0.0")

    @app.post("/webhook/jira")
    async def jira_webhook(request: Request) -> dict[str, str]:
        body = await request.body()

        # Verify signature if secret is configured
        if webhook_secret:
            sig_header = request.headers.get("X-Hub-Signature", "")
            try:
                verify_webhook_signature(webhook_secret, body, sig_header)
            except WebhookValidationError as exc:
                logger.warning("Webhook signature validation failed: %s", exc)
                raise HTTPException(status_code=401, detail=str(exc))

        # Parse the webhook payload
        try:
            import json
            payload: dict[str, Any] = json.loads(body)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid JSON payload")

        event = parse_webhook_event(payload)
        if event is None:
            return {"status": "ignored"}

        # Process the ticket asynchronously
        logger.info("Processing webhook event: %s", event)
        try:
            ticket = await intake_agent.process_from_webhook(event)
            logger.info("Ticket ready: %s", ticket.key)

            if on_ticket_ready:
                await on_ticket_ready(ticket)

        except Exception as exc:
            logger.error("Error processing webhook for %s: %s", event.ticket_key, exc)
            raise HTTPException(status_code=500, detail=f"Processing error: {exc}")

        return {"status": "accepted", "ticket": event.ticket_key}

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "safs-webhook"}

    return app
