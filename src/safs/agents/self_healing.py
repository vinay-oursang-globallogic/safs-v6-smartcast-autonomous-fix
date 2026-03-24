"""
SAFS v6.0 — Self-Healing Agent

Learns from developer feedback, PR rejections, and production regressions by
indexing corrections into Qdrant so future runs avoid repeating mistakes.

Three entry points:
- :meth:`process_developer_correction` — developer explicitly rejects a fix.
- :meth:`process_pr_rejection` — automated PR review rejects the draft.
- :meth:`process_production_regression` — telemetry detects regression
  post-merge.

Each method persists a :class:`~safs.qdrant_collections.models.CorrectionRecord`
and optionally updates the Jira ticket.

Example usage::

    agent = SelfHealingAgent(
        correction_indexer=CorrectionIndexer(qdrant_url=..., voyage_api_key=...),
        jira_client=JiraClient(base_url=..., username=..., api_token=...),
    )
    record = await agent.process_developer_correction(
        original_pr_url="https://github.com/org/repo/pull/99",
        correction_description="null-check was added in wrong branch",
        corrected_by="vinay@vizio.com",
    )
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from safs.qdrant_collections.correction_indexer import CorrectionIndexer
from safs.qdrant_collections.models import CorrectionRecord

logger = logging.getLogger(__name__)


class SelfHealingAgent:
    """
    Agent responsible for recording SAFS mistakes and corrections.

    Args:
        correction_indexer: Instance of :class:`~safs.qdrant_collections.correction_indexer.CorrectionIndexer`.
        jira_client: Optional Jira client for updating tickets.
            Omit to disable Jira updates.
    """

    def __init__(
        self,
        correction_indexer: CorrectionIndexer,
        jira_client: Optional[object] = None,
    ) -> None:
        self._indexer = correction_indexer
        self._jira = jira_client

    async def process_developer_correction(
        self,
        original_pr_url: str,
        correction_description: str,
        corrected_by: str,
        error_category: str = "UNKNOWN",
        jira_ticket: str = "",
    ) -> CorrectionRecord:
        """
        Record a correction made by a developer who rejected a SAFS-generated fix.

        Args:
            original_pr_url: URL of the rejected pull request.
            correction_description: Human description of what was wrong and
                what the right approach is.
            corrected_by: Email or username of the developer.
            error_category: ErrorCategory value if known.
            jira_ticket: Associated Jira ticket key.

        Returns:
            The persisted :class:`CorrectionRecord`.
        """
        record = CorrectionRecord(
            correction_id=str(uuid.uuid4()),
            original_fix_id=self._extract_fix_id_from_pr(original_pr_url),
            jira_ticket=jira_ticket or self._extract_ticket_from_pr(original_pr_url),
            error_category=error_category,
            mistake_type="DEVELOPER_REJECTION",
            description=(
                f"Developer correction for PR {original_pr_url}: "
                f"{correction_description}"
            ),
            what_went_wrong=correction_description,
            correct_approach=f"Corrected by {corrected_by}: {correction_description}",
            severity="HIGH",
            lesson_learned=correction_description,
            prevention_checklist=[
                "Review PR diff against original failing test",
                "Verify fix handles all edge cases described in Jira",
            ],
            detected_by="DEVELOPER",
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        correction_id = await self._indexer.index_correction(record)
        logger.info(
            "Indexed developer correction %s for PR %s",
            correction_id,
            original_pr_url,
        )

        if self._jira and jira_ticket:
            await self._add_jira_comment(
                jira_ticket,
                f"⚠️ SAFS fix was corrected by {corrected_by}.\n\n"
                f"*What went wrong:* {correction_description}\n\n"
                f"*Correction ID:* {correction_id}",
            )

        return record

    async def process_pr_rejection(
        self,
        pr_url: str,
        rejection_reason: str,
        error_category: str = "UNKNOWN",
        jira_ticket: str = "",
    ) -> CorrectionRecord:
        """
        Record an automated PR rejection (e.g., CI failure or review bot).

        Args:
            pr_url: URL of the rejected PR.
            rejection_reason: CI output, lint errors, or reviewer comments.
            error_category: ErrorCategory value if known.
            jira_ticket: Associated Jira ticket key.

        Returns:
            The persisted :class:`CorrectionRecord`.
        """
        record = CorrectionRecord(
            correction_id=str(uuid.uuid4()),
            original_fix_id=self._extract_fix_id_from_pr(pr_url),
            jira_ticket=jira_ticket or self._extract_ticket_from_pr(pr_url),
            error_category=error_category,
            mistake_type="AUTO_REJECTION",
            description=(
                f"Automated PR rejection for {pr_url}: {rejection_reason[:500]}"
            ),
            what_went_wrong=rejection_reason,
            correct_approach=(
                "Fix must pass all CI checks before submission. "
                "Review the rejection reason and address all issues."
            ),
            severity="MEDIUM",
            lesson_learned=f"PR rejected: {rejection_reason[:200]}",
            prevention_checklist=[
                "Run local tests before creating PR",
                "Check CI requirements in repository CONTRIBUTING.md",
            ],
            detected_by="AUTOMATED",
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        correction_id = await self._indexer.index_correction(record)
        logger.info(
            "Indexed PR rejection correction %s for %s",
            correction_id,
            pr_url,
        )
        return record

    async def process_production_regression(
        self,
        merged_pr_url: str,
        spike_factor: float,
        error_category: str = "UNKNOWN",
        jira_ticket: str = "",
        regression_metric: str = "error_rate",
    ) -> CorrectionRecord:
        """
        Record a production regression detected after a SAFS fix was merged.

        Args:
            merged_pr_url: URL of the merged PR that introduced the regression.
            spike_factor: How many times above baseline the error rate spiked.
            error_category: ErrorCategory value if known.
            jira_ticket: Associated Jira ticket key.
            regression_metric: Name of the metric that spiked.

        Returns:
            The persisted :class:`CorrectionRecord`.
        """
        record = CorrectionRecord(
            correction_id=str(uuid.uuid4()),
            original_fix_id=self._extract_fix_id_from_pr(merged_pr_url),
            jira_ticket=jira_ticket or self._extract_ticket_from_pr(merged_pr_url),
            error_category=error_category,
            mistake_type="REGRESSION",
            description=(
                f"Production regression after {merged_pr_url}: "
                f"{regression_metric} spiked {spike_factor:.1f}× above baseline"
            ),
            what_went_wrong=(
                f"Fix caused {regression_metric} to spike {spike_factor:.1f}× "
                f"above the 72h pre-merge baseline."
            ),
            correct_approach=(
                "Revert the merged PR or issue a hot-fix. "
                "Investigate side-effects of the code change on production traffic."
            ),
            severity="CRITICAL" if spike_factor >= 5.0 else "HIGH",
            lesson_learned=(
                f"Fix introduced regression: {regression_metric} × {spike_factor:.1f}. "
                "Include integration test covering regression path."
            ),
            prevention_checklist=[
                "Add regression test for the specific error path",
                "Enable 24h canary deployment before full rollout",
                "Verify fix does not change error rate for unrelated categories",
            ],
            detected_by="TELEMETRY",
            time_to_detect_hours=72.0,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        correction_id = await self._indexer.index_correction(record)
        logger.error(
            "Indexed regression correction %s (factor=%.1f) for PR %s",
            correction_id,
            spike_factor,
            merged_pr_url,
        )

        if self._jira and jira_ticket:
            await self._add_jira_comment(
                jira_ticket,
                f"🚨 SAFS-generated fix caused a production regression!\n\n"
                f"*PR:* {merged_pr_url}\n"
                f"*Metric:* {regression_metric} spiked {spike_factor:.1f}×\n"
                f"*Correction ID:* {correction_id}\n\n"
                "_Initiating revert investigation..._",
            )

        return record

    # ── Private ───────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_fix_id_from_pr(pr_url: str) -> Optional[str]:
        """Try to extract a SAFS fix UUID from PR URL query params or title."""
        m = re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", pr_url, re.I)
        return m.group(0) if m else None

    @staticmethod
    def _extract_ticket_from_pr(pr_url: str) -> str:
        """Try to extract a Jira ticket key (e.g., SMART-1234) from the PR URL."""
        m = re.search(r"[A-Z]{2,10}-\d+", pr_url)
        return m.group(0) if m else ""

    async def _add_jira_comment(self, ticket_key: str, comment: str) -> None:
        """Post *comment* to *ticket_key* if a Jira client is available."""
        if self._jira is None:
            return
        try:
            await self._jira.add_comment(ticket_key, comment)  # type: ignore[attr-defined]
        except Exception as exc:
            logger.warning(
                "Failed to add Jira comment to %s: %s", ticket_key, exc
            )
