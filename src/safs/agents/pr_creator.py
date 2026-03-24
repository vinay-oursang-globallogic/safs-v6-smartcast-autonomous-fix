"""
PR Creator Agent — Stage 8 / Phase 14

Creates draft pull requests via RepositoryAdapter abstraction.
Ported from jira_auto_fixer/github_client.py with v6.0 enhancements:
- RepositoryAdapter abstraction (GitHub/GitLab/Bitbucket)
- DRAFT PRs only (never direct merge)
- CROSS_LAYER: creates TWO PRs (LOKi repo + app repo)
- Includes validation and reproduction evidence in PR description
- Retry logic for API resilience

Master Prompt Reference: Section 3.12 - Stage 8: PR Creation

Usage:
    pr_creator = PRCreatorAgent(retrieval_router=router)
    result = await pr_creator.create(
        state=pipeline_state,
        candidate=best_fix_candidate,
        validation=validation_result,
        repro=reproduction_result,
    )
"""

import logging
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Any, List, Dict

from safs.log_analysis.models import (
    PipelineState,
    BugLayer,
    FixCandidate,
    ConfidenceRouting,
)
from safs.agents.confidence_ensemble import ConfidenceResult
from safs.reproduction.models import ReproResultV2
from safs.retrieval.retrieval_router import RetrievalRouter
from safs.retrieval.repository_adapter import FileChange

logger = logging.getLogger(__name__)


class PRResult:
    """Result of PR creation operation."""
    
    def __init__(
        self,
        pr_url: str,
        branch_name: str,
        pr_number: Optional[int] = None,
        secondary_pr_url: Optional[str] = None,
        secondary_branch: Optional[str] = None,
    ):
        self.pr_url = pr_url
        self.branch_name = branch_name
        self.pr_number = pr_number
        self.secondary_pr_url = secondary_pr_url
        self.secondary_branch = secondary_branch
        self.created_at = datetime.now(timezone.utc)


class PRCreatorAgent:
    """
    PR Creator Agent — Stage 8 / Phase 14
    
    Creates draft PRs via RepositoryAdapter abstraction.
    Extended from jira_auto_fixer/github_client.py POC with retry logic and CROSS_LAYER support.
    
    Master Prompt Rule #7: All PRs MUST be created as DRAFT by default.
    """
    
    def __init__(
        self,
        retrieval_router: Optional[RetrievalRouter] = None,
        github_token: Optional[str] = None,
        default_base_branch: str = "main",
        max_retries: int = 3,
        retry_delay: float = 2.0,
    ):
        """
        Initialize PR creator.
        
        Args:
            retrieval_router: RetrievalRouter for accessing RepositoryAdapter
            github_token: GitHub API token (for backward compatibility)
            default_base_branch: Default base branch for PRs
            max_retries: Maximum number of retries for API calls
            retry_delay: Delay between retries in seconds
        """
        self.router = retrieval_router
        self.github_token = github_token
        self.default_base_branch = default_base_branch
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        
        if not retrieval_router:
            # github_token alone is insufficient: PR operations require a
            # RepositoryAdapter (obtained via RetrievalRouter). If only a raw
            # token was supplied, warn clearly so callers aren't misled.
            logger.warning(
                "PRCreatorAgent: retrieval_router is None — PR creation will "
                "fail. Wrap the github_token in a RetrievalRouter with a "
                "GitHubMCPAdapter instead of passing it directly."
            )
        
        logger.info(f"PRCreatorAgent initialized (retries={max_retries})")
    
    async def create(
        self,
        state: PipelineState,
        candidate: FixCandidate,
        validation: Optional[Any] = None,
        repro: Optional[ReproResultV2] = None,
        confidence: Optional[ConfidenceResult] = None,
    ) -> PRResult:
        """
        Create a draft PR for a fix candidate.
        
        Implements Master Prompt Section 3.12:
        - Creates branch from base branch
        - Pushes file changes
        - Creates DRAFT PR with comprehensive description
        - For CROSS_LAYER bugs, creates secondary PR in companion repo
        
        Args:
            state: Pipeline state with ticket and analysis context
            candidate: Fix candidate to create PR for
            validation: Optional validation results
            repro: Optional reproduction results
            confidence: Optional confidence results
        
        Returns:
            PRResult with PR URL and metadata
        
        Raises:
            Exception if PR creation fails after retries
        """
        logger.info(f"🔨 Creating PR for {state.ticket.key} - {candidate.strategy.value}")
        
        # Get adapter for target repo
        if not self.router:
            token_hint = (
                " (github_token was supplied but is not used — wrap it in a "
                "RetrievalRouter with a GitHubMCPAdapter)"
                if self.github_token else ""
            )
            raise ValueError(f"RetrievalRouter required for PR creation{token_hint}")
        
        adapter = self.router.get_adapter(candidate.target_repo)
        if not adapter:
            raise ValueError(f"No adapter available for repo: {candidate.target_repo}")
        
        # Generate branch name
        branch_name = self._generate_branch_name(state.ticket.key, candidate)
        base_branch = candidate.target_branch or self.default_base_branch
        
        # Build PR title and body
        title = self._build_pr_title(state.ticket.key, candidate)
        body = self._build_pr_body(
            state=state,
            candidate=candidate,
            validation=validation,
            repro=repro,
            confidence=confidence,
        )
        
        try:
            # Step 1: Create branch with retry
            logger.info(f"   Creating branch: {branch_name}")
            await self._retry_operation(
                adapter.create_branch,
                candidate.target_repo,
                branch_name,
                base_branch,
            )
            
            # Step 2: Convert file_changes to FileChange objects
            file_changes = self._convert_to_file_changes(candidate.file_changes)
            
            # Step 3: Push files with retry
            logger.info(f"   Pushing {len(file_changes)} file(s)")
            commit_sha = await self._retry_operation(
                adapter.push_files,
                candidate.target_repo,
                branch_name,
                file_changes,
            )
            logger.info(f"   Files committed: {commit_sha[:8]}")
            
            # Step 4: Create draft PR with retry
            logger.info(f"   Creating draft PR")
            pr_url = await self._retry_operation(
                adapter.create_pull_request,
                repo=candidate.target_repo,
                title=title,
                body=body,
                head=branch_name,
                base=base_branch,
                draft=True,  # ALWAYS DRAFT per Master Prompt Rule #7
            )
            
            logger.info(f"   ✅ PR created: {pr_url}")
            
            # Step 5: Handle CROSS_LAYER - create secondary PR
            secondary_pr_url = None
            secondary_branch = None
            
            bug_layer = state.buglayer_result.layer if state.buglayer_result else None
            if bug_layer == BugLayer.CROSS_LAYER and candidate.has_secondary_fix:
                logger.info(f"   🔗 CROSS_LAYER: Creating secondary PR")
                secondary_result = await self._create_secondary_pr(
                    state=state,
                    candidate=candidate,
                    primary_pr_url=pr_url,
                    validation=validation,
                    repro=repro,
                    confidence=confidence,
                )
                secondary_pr_url = secondary_result.pr_url
                secondary_branch = secondary_result.branch_name
                logger.info(f"   ✅ Secondary PR created: {secondary_pr_url}")
            
            return PRResult(
                pr_url=pr_url,
                branch_name=branch_name,
                pr_number=None,  # Would be extracted from PR URL if needed
                secondary_pr_url=secondary_pr_url,
                secondary_branch=secondary_branch,
            )
            
        except Exception as e:
            logger.error(f"   ❌ PR creation failed: {e}", exc_info=True)
            raise
    
    async def _create_secondary_pr(
        self,
        state: PipelineState,
        candidate: FixCandidate,
        primary_pr_url: str,
        validation: Optional[Any] = None,
        repro: Optional[ReproResultV2] = None, 
        confidence: Optional[ConfidenceResult] = None,
    ) -> PRResult:
        """
        Create secondary PR for CROSS_LAYER fixes.
        
        Args:
            state: Pipeline state
            candidate: Fix candidate with secondary fix details
            primary_pr_url: URL of primary PR
            validation: Validation results
            repro: Reproduction results
            confidence: Confidence results
        
        Returns:
            PRResult for secondary PR
        """
        if not candidate.secondary_repo:
            raise ValueError("secondary_repo required for CROSS_LAYER fix")
        
        # Get adapter for secondary repo
        secondary_adapter = self.router.get_adapter(candidate.secondary_repo)
        if not secondary_adapter:
            raise ValueError(f"No adapter for secondary repo: {candidate.secondary_repo}")
        
        # Generate secondary branch name
        secondary_branch = f"safs/{state.ticket.key.lower()}/secondary-{candidate.strategy.value.lower()}"
        base_branch = candidate.target_branch or self.default_base_branch
        
        #Build secondary PR title and body
        secondary_title = f"[SAFS] {state.ticket.key}: {candidate.secondary_summary or candidate.summary} (Companion)"
        secondary_body = self._build_pr_body(
            state=state,
            candidate=candidate,
            validation=validation,
            repro=repro,
            confidence=confidence,
            is_secondary=True,
            primary_pr_url=primary_pr_url,
        )
        
        # Create branch
        await self._retry_operation(
            secondary_adapter.create_branch,
            candidate.secondary_repo,
            secondary_branch,
            base_branch,
        )
        
        # Push files
        secondary_files = self._convert_to_file_changes(candidate.secondary_file_changes)
        await self._retry_operation(
            secondary_adapter.push_files,
            candidate.secondary_repo,
            secondary_branch,
            secondary_files,
        )
        
        # Create PR
        secondary_pr_url = await self._retry_operation(
            secondary_adapter.create_pull_request,
            repo=candidate.secondary_repo,
            title=secondary_title,
            body=secondary_body,
            head=secondary_branch,
            base=base_branch,
            draft=True,
        )
        
        return PRResult(
            pr_url=secondary_pr_url,
            branch_name=secondary_branch,
            pr_number=None,
        )
    
    async def _retry_operation(self, operation, *args, **kwargs):
        """
        Retry an async operation with exponential backoff.
        
        Args:
            operation: Async callable to retry
            *args: Positional arguments
            **kwargs: Keyword arguments
        
        Returns:
            Result of operation
        
        Raises:
            Exception if all retries fail
        """
        last_exception = None
        
        for attempt in range(self.max_retries):
            try:
                return await operation(*args, **kwargs)
            except Exception as e:
                last_exception = e
                if attempt < self.max_retries - 1:
                    delay = self.retry_delay * (2 ** attempt)  # Exponential backoff
                    logger.warning(f"   Retry {attempt + 1}/{self.max_retries} after {delay}s: {e}")
                    await asyncio.sleep(delay)
                else:
                    logger.error(f"   Operation failed after {self.max_retries} attempts")
        
        raise last_exception
    
    def _convert_to_file_changes(self, file_changes: List[Dict[str, Any]]) -> List[FileChange]:
        """
        Convert file_changes dict list to FileChange objects.
        
        Args:
            file_changes: List of file change dictionaries
        
        Returns:
            List of FileChange objects
        """
        result = []
        for change in file_changes:
            result.append(
                FileChange(
                    path=change.get("path", ""),
                    content=change.get("content", ""),
                    operation=change.get("operation", "update"),
                )
            )
        return result
    
    def _generate_branch_name(self, ticket_key: str, candidate: FixCandidate) -> str:
        """Generate branch name for PR."""
        strategy = candidate.strategy.value.lower()
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        return f"safs/{ticket_key.lower()}/{strategy}-{timestamp}"
    
    def _build_pr_title(self, ticket_key: str, candidate: FixCandidate) -> str:
        """Build PR title."""
        # Use summary if available, otherwise use strategy name
        summary = candidate.summary if candidate.summary else candidate.strategy.value
        return f"[SAFS] {ticket_key}: {summary[:80]}"
    
    def _build_pr_body(
        self,
        state: PipelineState,
        candidate: FixCandidate,
        validation: Optional[Any],
        repro: Optional[ReproResultV2],
        confidence: Optional[ConfidenceResult],
        is_secondary: bool = False,
        primary_pr_url: Optional[str] = None,
    ) -> str:
        """
        Build comprehensive PR description with all evidence.
        
        Includes:
        - Ticket link and summary
        - Root cause analysis
        - Fix strategy and explanation
        - Validation results (if available)
        - Reproduction evidence (if available)
        - Confidence score and routing decision
        - Link to paired PR (for CROSS_LAYER secondary PRs)
        
        Args:
            state: Pipeline state
            candidate: Fix candidate
            validation: Validation results
            repro: Reproduction results
            confidence: Confidence results
            is_secondary: Whether this is a secondary CROSS_LAYER PR
            primary_pr_url: URL of primary PR (for secondary PRs)
        
        Returns:
            Formatted PR description in Markdown
        """
        body_parts = []
        
        # Header
        if is_secondary:
            body_parts.append("## 🤖 SAFS v6.0 Automated Fix (CROSS_LAYER Companion)\n")
        else:
            body_parts.append("## 🤖 SAFS v6.0 Automated Fix\n")
        
        body_parts.append(f"**Ticket**: {state.ticket.key}")
        body_parts.append(f"**Strategy**: {candidate.strategy.value}")
        bug_layer = state.buglayer_result.layer if state.buglayer_result else None
        body_parts.append(f"**Bug Layer**: {bug_layer.value if bug_layer else 'UNKNOWN'}")
        
        if is_secondary and primary_pr_url:
            body_parts.append(f"**Primary PR**: {primary_pr_url}")
        
        body_parts.append("")
        
        # Root cause
        if state.root_cause_result:
            body_parts.append("## 🎯 Root Cause Analysis\n")
            body_parts.append(f"**Category**: {state.root_cause_result.error_category.value}")
            body_parts.append(f"**Severity**: {state.root_cause_result.severity.value}")
            body_parts.append(f"**Confidence**: {state.root_cause_result.confidence:.2%}")
            body_parts.append(f"\n{state.root_cause_result.root_cause}\n")
        
        # Fix explanation
        body_parts.append("## 🔧 Fix Explanation\n")
        if is_secondary and candidate.secondary_summary:
            body_parts.append(f"{candidate.secondary_summary}\n")
        else:
            body_parts.append(f"{candidate.explanation}\n")
        
        # Reproduction evidence
        if repro and repro.status.value == "REPRODUCED":
            body_parts.append("## 🐛 Bug Reproduction\n")
            body_parts.append("✅ Bug successfully reproduced on dev TV before fix generation")
            if repro.device_info:
                body_parts.append(f"\n**Device Info**:")
                body_parts.append(f"- Firmware: {repro.device_info.firmware_version}")
                body_parts.append(f"- Chipset: {repro.device_info.chipset}")
            body_parts.append("")
        
        # Validation results
        if validation:
            body_parts.append("## ✅ Validation Results\n")
            
            # Alpha path (QEMU)
            if hasattr(validation, 'alpha_result') and validation.alpha_result:
                status = "✅ PASSED" if validation.alpha_result.passed else "❌ FAILED"
                body_parts.append(f"**PATH α (QEMU)**: {status}")
            
            # Beta path (Playwright)
            if hasattr(validation, 'beta_result') and validation.beta_result:
                status = "✅ PASSED" if validation.beta_result.passed else "❌ FAILED"
                body_parts.append(f"**PATH β (Playwright)**: {status}")
            
            # Gamma path (On-Device)
            if hasattr(validation, 'gamma_result') and validation.gamma_result:
                status = "✅ PASSED" if validation.gamma_result.passed else "❌ FAILED"
                body_parts.append(f"**PATH γ (On-Device)**: {status}")
            
            body_parts.append("")
        
        # Confidence score
        if confidence:
            body_parts.append("## 📊 Confidence Assessment\n")
            body_parts.append(f"**Score**: {confidence.calibrated_score:.1%}")
            body_parts.append(f"**Routing**: {confidence.routing.value}")
            
            if confidence.routing == ConfidenceRouting.AUTO_PR:
                body_parts.append("\n⚠️ **High confidence** - Consider auto-merge after review")
            elif confidence.routing == ConfidenceRouting.PR_WITH_REVIEW:
                body_parts.append("\n⚠️ **Medium confidence** - Manual review required before merge")
            else:
                body_parts.append("\n⚠️ **Low confidence** - Careful review and testing required")
            
            body_parts.append("")
        
        # Footer
        body_parts.append("---")
        body_parts.append("*This PR was automatically generated by SAFS v6.0*")
        body_parts.append("*All PRs are created as DRAFT by default - manual review required before merge*")
        
        return "\n".join(body_parts)
    
    async def update_pr(
        self,
        pr_url: str,
        comment: str,
    ) -> bool:
        """
        Add a comment to an existing PR.
        
        Args:
            pr_url: PR URL to comment on
            comment: Comment text
        
        Returns:
            True if successful
        """
        logger.info(f"Would add comment to PR: {pr_url}")
        logger.info(f"   Comment: {comment[:100]}...")
        return True
    
    async def close_pr(
        self,
        pr_url: str,
        reason: str,
    ) -> bool:
        """
        Close a PR with a reason.
        
        Args:
            pr_url: PR URL to close
            reason: Reason for closing
        
        Returns:
            True if successful
        """
        logger.info(f"Would close PR: {pr_url}")
        logger.info(f"   Reason: {reason}")
        return True
