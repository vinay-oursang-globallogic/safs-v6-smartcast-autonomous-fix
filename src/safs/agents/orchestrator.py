"""
SAFS v6.0 Pipeline Orchestrator

Orchestrates all 12 phases of the SmartCast Autonomous Fix System pipeline:
- Stage -1: Quality Gate
- Stage 0: BugLayerRouter
- Stages 1-2: Log Intelligence (Parsing + Symbolication)
- Stage 3: Root Cause Analysis
- Stage 4: Repo Locator
- Stage 5: Context Builder
- Stage 5.5: Bug Reproduction
- Stage 6: Fix Generation (3-candidate tournament)
- Stage 7: Tri-Path Validation
- Stage 7.5: Confidence Ensemble
- Stage 8: PR Creation

Usage:
    orchestrator = SAFSOrchestrator()
    result = await orchestrator.run(ticket_key="SMART-12345")
"""

import asyncio
import logging
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone

from safs.log_analysis.models import PipelineState, BugLayer, ConfidenceRouting, JiraTicket, FixCandidate
from safs.log_analysis.quality_gate import LogQualityGate
from safs.log_analysis.bug_layer_router import BugLayerRouter
from safs.log_intelligence.agent import LogIntelligenceAgent
from safs.root_cause_analysis.agent import RootCauseAgent
from safs.root_cause_analysis.llm_client import LLMClient
from safs.context.context_builder import ContextBuilderAgent
from safs.reproduction.agent import BugReproductionAgent
from safs.validation.tri_path_validator import TriPathValidator
from safs.retrieval.retrieval_router import RetrievalRouter
from safs.telemetry.regression_test_generator import RegressionTestGenerator
from .repo_locator import RepoLocatorAgent
from .fix_generator import FixGeneratorAgent
from .confidence_ensemble import (
    ConfidenceEnsemble,
    ConfidenceResult,
    ConfidenceSignals,
    build_confidence_signals,
)
from .pr_creator import PRCreatorAgent

logger = logging.getLogger(__name__)


class SAFSOrchestrator:
    """
    Main SAFS v6.0 Pipeline Orchestrator
    
    Wires together all 12 phases from quality gate through PR creation.
    Each stage updates PipelineState and passes results to the next stage.
    """
    
    def __init__(
        self,
        workspace_root: Optional[Path] = None,
        qdrant_url: str = "http://localhost:6333",
        github_token: Optional[str] = None,
        anthropic_api_key: Optional[str] = None,
    ):
        """
        Initialize orchestrator with all stage agents.
        
        Args:
            workspace_root: Root directory for code repositories
            qdrant_url: Qdrant vector DB URL for retrieval
            github_token: GitHub API token for PR creation
            anthropic_api_key: Anthropic API key (falls back to ANTHROPIC_API_KEY env var)
        """
        import os

        self.workspace_root = workspace_root or Path.cwd()
        self.qdrant_url = qdrant_url
        self.github_token = github_token
        
        # Resolve API key at init time with clear error message
        resolved_key = anthropic_api_key or os.getenv("ANTHROPIC_API_KEY")
        if not resolved_key:
            raise ValueError(
                "Anthropic API key is required. Set ANTHROPIC_API_KEY env var "
                "or pass anthropic_api_key to SAFSOrchestrator()."
            )
        
        # Initialize retrieval infrastructure
        self.retrieval_router = RetrievalRouter(
            # Initialize with empty/None values - real agents will configure as needed
            github_mcp=None,
            code_index_mcp=None,
            qdrant_client=None,  # Qdrant initialized separately if needed
        )
        
        # Initialize all stage agents
        self.quality_gate = LogQualityGate()
        self.bug_layer_router = BugLayerRouter()
        self.log_intelligence = LogIntelligenceAgent()
        
        # Initialize LLM client (shared across agents) with explicit API key
        self.llm = LLMClient(api_key=resolved_key)
        
        self.root_cause = RootCauseAgent(api_key=resolved_key)
        self.repo_locator = RepoLocatorAgent(
            retrieval_router=self.retrieval_router,
        )
        self.context_builder = ContextBuilderAgent(
            retrieval_router=self.retrieval_router,
        )
        self.reproduction = BugReproductionAgent()
        self.fix_generator = FixGeneratorAgent(llm_client=self.llm)
        self.validator = TriPathValidator()
        self.confidence_ensemble = ConfidenceEnsemble()
        self.pr_creator = PRCreatorAgent(
            retrieval_router=self.retrieval_router,
            github_token=self.github_token,
        )
        
        # Phase 13: Async telemetry systems (post-PR)
        self.test_generator = RegressionTestGenerator(llm_client=self.llm)
        
        logger.info("SAFSOrchestrator initialized with 12-phase pipeline")
    
    async def run(
        self,
        ticket_key: str,
        log_files: list[Path],
        jira_created_at: Optional[datetime] = None,
        skip_validation: bool = False,
        skip_reproduction: bool = False,
        dry_run: bool = False,
    ) -> PipelineState:
        """
        Run complete SAFS v6.0 pipeline on a ticket.
        
        Args:
            ticket_key: Jira ticket key (e.g., "SMART-12345")
            log_files: List of log file paths to analyze
            jira_created_at: Ticket creation time for time-window filtering
            skip_validation: Skip tri-path validation (for testing)
            skip_reproduction: Skip bug reproduction (for testing)
            dry_run: Skip PR creation (for testing/preview)
        
        Returns:
            PipelineState with all stage results populated
        """
        # Create JiraTicket object from ticket_key with attachments.
        # Wrap each Path into a LogFile model so Attachment.log_files has the
        # right type and quality_gate.assess() can access .path_to_file on each.
        from safs.log_analysis.models import Attachment, LogFile as _LogFile
        log_file_models: list[_LogFile] = [
            _LogFile(
                path_to_file=str(p),
                path_from_log_root=p.name,
                attachment_filename=p.name,
                from_archive=False,
            )
            for p in log_files
        ]
        attachments = []
        if log_file_models:
            attachment = Attachment(
                id="synthetic-attachment",
                filename="logs.zip",
                size=0,
                mime_type="application/zip",
                content_url="",
                log_files=log_file_models,
            )
            attachments.append(attachment)
        
        ticket = JiraTicket(key=ticket_key, attachments=attachments)
        
        state = PipelineState(
            ticket=ticket,
            current_stage="INIT",
        )
        
        try:
            logger.info(f"🚀 Starting SAFS v6.0 pipeline for {ticket_key}")
            
            # Stage -1: Quality Gate
            # Pass the LogFile model list (not raw Paths) that was built above.
            state.current_stage = "QUALITY_GATE"
            logger.info(f"📊 Stage -1: Quality Gate")
            quality_result = await self.quality_gate.assess(
                log_files=log_file_models,
                jira_ticket=ticket,
            )
            state.quality_result = quality_result
            
            if not quality_result.passed:
                reason = quality_result.reasons[0] if quality_result.reasons else "Unknown reason"
                logger.warning(f"❌ Quality gate failed: {reason}")
                state.current_stage = "FAILED_QUALITY_GATE"
                return state
            
            # Stage 0: BugLayer Router
            state.current_stage = "BUG_LAYER_ROUTING"
            logger.info(f"🔀 Stage 0: BugLayer Router")
            buglayer_result = self.bug_layer_router.route(state)
            state.buglayer_result = buglayer_result
            
            # Set ticket priority for rate limiting (Master Prompt Rule #5)
            state.ticket_priority = self._map_ticket_priority(ticket)
            
            logger.info(f"   Bug Layer: {buglayer_result.layer.value} "
                       f"(confidence: {buglayer_result.confidence:.2f})")
            logger.info(f"   Priority: {state.ticket_priority}")
            
            # Stage 1-2: Log Intelligence
            # Extract context keywords from ticket description/summary so the
            # SmartTVErrorAnalyzer can weight error matches against Jira context.
            context_keywords: list[str] | None = None
            ticket_text = " ".join(filter(None, [ticket.description, ticket.summary]))
            if ticket_text.strip():
                context_keywords = [
                    w.lower() for w in ticket_text.split()
                    if len(w) >= 4 and w.isalpha()
                ][:20]
            state.current_stage = "LOG_INTELLIGENCE"
            logger.info(f"🔍 Stages 1-2: Log Intelligence (Parsing + Symbolication)")
            log_analysis = await self.log_intelligence.analyze(
                log_lines=quality_result.filtered_logs,
                bug_layer=buglayer_result.layer,
                context_keywords=context_keywords or [],
            )
            state.log_analysis_result = log_analysis
            
            # Stage 3: Root Cause Analysis
            state.current_stage = "ROOT_CAUSE"
            logger.info(f"🎯 Stage 3: Root Cause Analysis")
            root_cause = await self.root_cause.analyze(
                state=state,
                log_analysis=log_analysis,
            )
            state.root_cause_result = root_cause
            
            logger.info(f"   Root Cause: {root_cause.root_cause[:100]}...")
            logger.info(f"   Confidence: {root_cause.confidence:.2f}")
            
            # Stage 4: Repo Locator
            # Convert the string priority (e.g. "P1") to the Priority IntEnum
            # expected by RepoLocatorAgent.locate().
            from safs.retrieval.rate_limiter import Priority as _Priority
            _str_to_priority = {"P0": _Priority.P0, "P1": _Priority.P1, "P2": _Priority.P2, "P3": _Priority.P3}
            locate_priority = _str_to_priority.get(state.ticket_priority, _Priority.P1)
            state.current_stage = "REPO_LOCATOR"
            logger.info(f"📂 Stage 4: Repo Locator")
            context_result = await self.repo_locator.locate(
                root_cause=root_cause,
                category=root_cause.error_category,
                device_id=None,
                priority=locate_priority,
            )
            # Do NOT store RepoLocatorResult in state.context_result here — it is the
            # wrong Pydantic type (ContextResult).  context_result is consumed directly
            # by ContextBuilderAgent below; state.context_result is set once we have
            # the properly typed ContextResult back from the builder.
            logger.info(f"   Located {len(context_result.primary_locations)} code locations")
            
            # Stage 5: Context Builder
            state.current_stage = "CONTEXT_BUILDER"
            logger.info(f"📝 Stage 5: Context Builder")
            enriched_context = await self.context_builder.build_context(
                state=state,
                repo_locator_result=context_result,
                root_cause_result=root_cause,
            )
            # Update context_result with enriched context
            state.context_result = enriched_context
            
            # Stage 5.5: Bug Reproduction (optional)
            if not skip_reproduction:
                state.current_stage = "REPRODUCTION"
                logger.info(f"🐛 Stage 5.5: Bug Reproduction")
                try:
                    repro_result = await self.reproduction.attempt(
                        state=state,
                    )
                    state.repro_result = repro_result
                    logger.info(f"   Reproduction: {repro_result.status.value}")
                except Exception as e:
                    logger.warning(f"   Reproduction failed: {e}")
                    # Continue pipeline even if reproduction fails
            
            # Stage 6: Fix Generation
            state.current_stage = "FIX_GENERATION"
            logger.info(f"🔧 Stage 6: Fix Generation (3-candidate tournament)")
            fix_candidates = await self.fix_generator.generate(
                state=state,
                root_cause=root_cause,
                context=enriched_context,
                repro=state.repro_result,
            )
            state.fix_candidates = fix_candidates
            
            logger.info(f"   Generated {len(fix_candidates)} fix candidates")
            for i, candidate in enumerate(fix_candidates, 1):
                logger.info(f"     [{i}] {candidate.strategy.value} "
                          f"(confidence: {candidate.confidence:.2f})")
            
            # Stage 7: Tri-Path Validation (optional)
            # Build a mapping from fix_id → CandidateValidationResult so that
            # after candidates are sorted by confidence the correct validation
            # result can be looked up for the best candidate in Stage 8.
            _val_by_fix_id: dict[str, object] = {}
            if not skip_validation:
                state.current_stage = "VALIDATION"
                logger.info(f"✅ Stage 7: Tri-Path Validation")
                try:
                    validation_results = await self.validator.validate_all(
                        state=state,
                        candidates=fix_candidates,
                    )
                    state.validation_results = validation_results
                    
                    # Update candidates with validation results.
                    # CandidateValidationResult uses `overall_passed`; evidence
                    # must be collected from per-path failure_reasons.
                    for candidate, val_res in zip(fix_candidates, validation_results):
                        candidate.validation_passed = val_res.overall_passed
                        reasons: list[str] = []
                        for path_res in filter(None, [
                            val_res.alpha_result,
                            val_res.beta_result,
                            val_res.gamma_result,
                        ]):
                            reasons.extend(path_res.failure_reasons)
                        candidate.validation_evidence = "; ".join(reasons) if reasons else "all paths passed"
                        _val_by_fix_id[candidate.fix_id] = val_res
                    
                    passed_count = sum(1 for r in validation_results if r.overall_passed)
                    logger.info(f"   Validation complete: "
                              f"{passed_count}/{len(fix_candidates)} passed")
                except Exception as e:
                    logger.warning(f"   Validation failed: {e}")
                    # Continue pipeline even if validation fails
            
            # Stage 7.5: Confidence Ensemble
            state.current_stage = "CONFIDENCE_ENSEMBLE"
            logger.info(f"🎯 Stage 7.5: Confidence Ensemble")
            
            # Compute confidence for each candidate
            for i, candidate in enumerate(fix_candidates):
                # Get validation result for this candidate
                validation_result = None
                if hasattr(state, 'validation_results') and state.validation_results:
                    validation_result = state.validation_results[i] if i < len(state.validation_results) else None
                
                # ReproResultV2 uses the field name `reproducible`  (not `reproduced`).
                _reproduction_ok = (
                    getattr(state.repro_result, 'reproducible', False)
                    if state.repro_result is not None else False
                )
                # ContextResult has no retrieval_score field; derive a proxy
                # from the number of qdrant semantic hits (0-10 → 0.1-0.9).
                _qdrant_hits = len(enriched_context.qdrant_results) if enriched_context.qdrant_results else 0
                _retrieval_sim = min(0.1 + _qdrant_hits * 0.08, 0.9)
                signals = build_confidence_signals(
                    candidate=candidate,
                    validation_result=validation_result,
                    historical_success_rate=0.7,
                    retrieval_similarity=_retrieval_sim,
                    reproduction_successful=_reproduction_ok,
                    bug_layer=buglayer_result.layer,
                    error_category=root_cause.error_category,
                )
                
                confidence_result = self.confidence_ensemble.compute(signals)
                candidate.ensemble_confidence = confidence_result.calibrated_score
                candidate.routing = confidence_result.routing
                
                logger.info(f"   {candidate.strategy.value}: "
                          f"confidence={confidence_result.calibrated_score:.3f}, "
                          f"routing={confidence_result.routing.value}")
            
            # Sort candidates by confidence
            fix_candidates.sort(key=lambda c: c.ensemble_confidence, reverse=True)
            state.fix_candidates = fix_candidates

            if not fix_candidates:
                logger.error("Fix generation produced zero candidates — aborting pipeline")
                state.current_stage = "FAILED_FIX_GENERATION"
                state.errors.append("No fix candidates generated")
                return state

            best_candidate = fix_candidates[0]
            logger.info(f"   🏆 Best candidate: {best_candidate.strategy.value} "
                       f"(confidence: {best_candidate.ensemble_confidence:.3f})")
            
            # Stage 8: PR Creation (implemented separately)
            state.current_stage = "PR_CREATION"
            logger.info(f"📤 Stage 8: PR Creation")
            
            # Check dry_run flag
            if dry_run:
                logger.info(f"   Dry run mode - skipping PR creation")
                state.pr_url = "DRY_RUN_MODE"
            # Check if we should create a PR based on routing decision
            elif best_candidate.routing in [ConfidenceRouting.AUTO_PR, 
                                         ConfidenceRouting.PR_WITH_REVIEW]:
                
                try:
                    # Look up the validation result for the *best* candidate
                    # (not index 0 of state.validation_results, which is in the
                    # original pre-sort order).
                    validation_result = _val_by_fix_id.get(best_candidate.fix_id)
                    
                    pr_result = await self.pr_creator.create(
                        state=state,
                        candidate=best_candidate,
                        validation=validation_result,
                        repro=state.repro_result if hasattr(state, 'repro_result') else None,
                        confidence=ConfidenceResult(
                            raw_score=best_candidate.ensemble_confidence or 0.0,
                            calibrated_score=best_candidate.ensemble_confidence or 0.0,
                            routing=best_candidate.routing,
                            signals=ConfidenceSignals(
                                llm_confidence=best_candidate.confidence,
                                retrieval_similarity=0.5,
                                validation_score=0.0,
                                historical_success_rate=0.7,
                            ),
                            signal_weights=self.confidence_ensemble.WEIGHTS,
                        ),
                    )
                    state.pr_url = pr_result.pr_url
                    logger.info(f"   ✅ PR created: {pr_result.pr_url}")
                    
                    # Phase 13: Async regression test generation (fire and forget)
                    asyncio.create_task(self._generate_regression_test(
                        state=state,
                        fix=best_candidate,
                        pr_branch=pr_result.branch_name,
                    ))
                    
                except Exception as e:
                    logger.error(f"   ❌ PR creation failed: {e}")
                    # Continue pipeline even if PR creation fails
            else:
                logger.info(f"   Routing: {best_candidate.routing.value} - No PR created")
            
            # Pipeline complete
            state.current_stage = "COMPLETE"
            state.completed_at = datetime.now(timezone.utc)
            duration = (state.completed_at - state.started_at).total_seconds()
            
            logger.info(f"✅ SAFS v6.0 pipeline complete for {ticket_key} "
                       f"in {duration:.1f}s")
            logger.info(f"   Bug Layer: {buglayer_result.layer.value}")
            logger.info(f"   Root Cause: {root_cause.root_cause[:100]}...")
            logger.info(f"   Best Fix: {best_candidate.strategy.value} "
                       f"(confidence: {best_candidate.ensemble_confidence:.3f})")
            logger.info(f"   Routing: {best_candidate.routing.value}")
            
            return state
            
        except Exception as e:
            logger.error(f"❌ Pipeline failed at stage {state.current_stage}: {e}", 
                       exc_info=True)
            state.errors.append(f"{type(e).__name__} in {state.current_stage}: {e}")
            state.current_stage = f"FAILED_{state.current_stage}"
            raise
    
    def _map_ticket_priority(self, ticket: JiraTicket) -> str:
        """
        Map Jira priority to rate limiter priority (Master Prompt Rule #5).
        
        P0/P1 = 5 calls/min reserved
        P2/P3 = 3 calls/min
        
        Args:
            ticket: Jira ticket
        
        Returns:
            Priority string (P0/P1/P2/P3)
        """
        priority = ticket.priority.upper() if ticket.priority else "P2"
        
        # Map common Jira priority names to P0-P3
        priority_map = {
            "BLOCKER": "P0",
            "CRITICAL": "P0",
            "HIGHEST": "P0",
            "HIGH": "P1",
            "MEDIUM": "P2",
            "LOW": "P3",
            "LOWEST": "P3",
        }
        
        # If already in P0-P3 format, use as-is
        if priority in ["P0", "P1", "P2", "P3"]:
            return priority
        
        # Map from Jira priority name
        return priority_map.get(priority, "P2")
    
    async def _generate_regression_test(
        self,
        state: PipelineState,
        fix: FixCandidate,
        pr_branch: str,
    ) -> None:
        """
        Generate regression test asynchronously after PR creation.
        
        Phase 13: Async post-PR telemetry system.
        Runs in background, does not block main pipeline.
        
        Args:
            state: Pipeline state
            fix: Fix candidate for which to generate test
            pr_branch: PR branch name
        """
        try:
            logger.info(f"🧪 Generating regression test for {state.ticket.key} (async)")
            success = await self.test_generator.generate_and_commit(
                state=state,
                fix=fix,
                pr_branch=pr_branch,
                repo_path=self.workspace_root,
            )
            if success:
                logger.info(f"   ✅ Regression test generated and committed")
            else:
                logger.warning(f"   ⚠️ Regression test generation skipped or failed")
        except Exception as e:
            logger.error(f"   ❌ Regression test generation error: {e}", exc_info=True)
            # Don't fail the pipeline for telemetry errors
