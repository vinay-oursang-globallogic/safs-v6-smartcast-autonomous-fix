"""
JIRA Intake Module
==================

Handles JIRA webhook events, attachment downloads, and ticket parsing.

Components:
- jira_webhook.py: Webhook handler + JiraIntakeAgent
- jira_client.py: JIRA REST API client
- attachment_handler.py: Attachment download & ZIP extraction
- keyword_extractor.py: NLP keyword extraction from descriptions
"""

from .attachment_handler import AttachmentHandler
from .jira_client import JiraClient
from .jira_webhook import (
    JiraIntakeAgent,
    WebhookEvent,
    parse_webhook_event,
    verify_webhook_signature,
)
from .keyword_extractor import KeywordExtractor

__all__ = [
    "AttachmentHandler",
    "JiraClient",
    "JiraIntakeAgent",
    "KeywordExtractor",
    "WebhookEvent",
    "parse_webhook_event",
    "verify_webhook_signature",
]
