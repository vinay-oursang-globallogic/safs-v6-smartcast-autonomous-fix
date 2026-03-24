"""
Fix Generation Agent — Stage 5.6

Generates three fix candidates in parallel using Claude Opus 4:
- SURGICAL: Minimal change to fix exact issue
- DEFENSIVE: Broader fix with guards against related failures
- REFACTORED: Structural improvement eliminating root cause class

Extended from jira_auto_fixer/orchestrator.py POC with:
- 3-candidate tournament (vs single-candidate)
- Layer-specific system prompts (LOKi C++14, HTML5 JS, CROSS_LAYER)
- Historical context from Qdrant (with temporal decay warnings)
- Known mistakes integration (anti-patterns to avoid)
- Reproduction evidence integration (NEW in v6.0)

Usage:
    fix_gen = FixGeneratorAgent(llm_client=llm)
    candidates = await fix_gen.generate(
        state=pipeline_state,
        root_cause=root_cause_result,
        context=context_result,
        repro=repro_result,  # Optional, from Phase 9
    )
    
    # Returns 3 FixCandidate objects with strategy, confidence, diff
"""

import asyncio
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone

from safs.log_analysis.models import (
    PipelineState,
    BugLayer,
    FixCandidate,
    FixStrategy,
    ConfidenceRouting,
    RootCauseResult,
    ContextResult,
)
from safs.reproduction.models import ReproResultV2, ReproductionStatus
from safs.root_cause_analysis.llm_client import LLMClient
from .repo_locator import CodeLocation
from .prompts import (
    LOKI_FIX_SYSTEM_PROMPT,
    HTML5_FIX_SYSTEM_PROMPT,
    CROSS_LAYER_FIX_SYSTEM_PROMPT,
    FixStrategy as PromptFixStrategy,
    get_strategy_guidance,
)


logger = logging.getLogger(__name__)


class FixGeneratorAgent:
    """
    Fix Generation Agent — Stage 5.6
    
    Generates three fix candidates in parallel, each with a different strategy:
    - SURGICAL: Minimal change to fix the exact reported issue
    - DEFENSIVE: Broader fix with guards against related failure modes
    - REFACTORED: Structural improvement that eliminates the root cause class
    
    Extended from jira_auto_fixer's single-candidate approach to a 3-candidate
    tournament. The jira_auto_fixer provides the orchestration skeleton — this
    extends it with layer-specific system prompts and multi-strategy generation.
    
    Model: Claude Opus 4 (highest capability for code generation)
    
    Master Prompt Reference: Section 3.9 - Stage 6: Fix Generation
    """
    
    # 3-strategy tournament (Master Prompt Section 3.9)
    STRATEGIES = [FixStrategy.NULL_CHECK, FixStrategy.MUTEX_GUARD, FixStrategy.SMART_POINTER]  # matches _map_strategy_to_enum
    
    # Map to prompt strategies (simplified for MVP)
    STRATEGY_NAMES = ["SURGICAL", "DEFENSIVE", "REFACTORED"]
    
    def __init__(self, llm_client: LLMClient):
        """
        Initialize Fix Generator Agent.
        
        Args:
            llm_client: LLMClient configured for Claude Opus 4
        """
        self.llm = llm_client
        logger.info("FixGeneratorAgent initialized with Claude Opus 4")
    
    async def generate(
        self,
        state: PipelineState,
        root_cause: RootCauseResult,
        context: ContextResult,
        repro: Optional[ReproResultV2] = None,
    ) -> List[FixCandidate]:
        """
        Generate three fix candidates in parallel.
        
        Args:
            state: PipelineState with ticket, bug layer, error category
            root_cause: Root cause analysis result
            context: ContextResult from Stage 5 (ContextBuilder output with code locations, similar fixes, mistakes)
            repro: Reproduction result (optional, NEW in v6.0)
            
        Returns:
            List of 3 FixCandidate objects (SURGICAL, DEFENSIVE, REFACTORED)
        """
        logger.info(f"Generating 3 fix candidates for {state.ticket.key}")
        
        # Step 1: Build layer-specific system prompt
        system_prompt = self._build_system_prompt(state.buglayer_result.layer)
        
        # Step 2: Format historical fix context with age warnings
        historical_context = self._format_historical_fixes(context.similar_fixes)
        
        # Step 3: Format known mistakes to avoid
        mistake_context = self._format_mistakes(context.known_mistakes)
        
        # Step 4: Format reproduction evidence (NEW in v6.0)
        repro_context = self._format_repro_evidence(repro)
        
        # Step 5: Generate 3 candidates in parallel (asyncio.gather)
        logger.info("Starting parallel 3-candidate generation...")
        start_time = datetime.now(timezone.utc)
        
        candidates = await asyncio.gather(*[
            self._generate_one(
                strategy=strategy_name,
                system_prompt=system_prompt,
                root_cause=root_cause,
                context=context,
                historical=historical_context,
                mistakes=mistake_context,
                repro=repro_context,
                state=state,
            )
            for strategy_name in self.STRATEGY_NAMES
        ])
        
        elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
        logger.info(f"Generated 3 candidates in {elapsed:.1f}s")
        
        return candidates
    
    def _build_system_prompt(self, bug_layer: BugLayer) -> str:
        """
        Select layer-specific system prompt.
        
        Args:
            bug_layer: BugLayer enum (LOKI, HTML5, CROSS_LAYER, MEDIATEK)
            
        Returns:
            Layer-specific system prompt string
        """
        if bug_layer == BugLayer.LOKI:
            return LOKI_FIX_SYSTEM_PROMPT
        elif bug_layer == BugLayer.HTML5:
            return HTML5_FIX_SYSTEM_PROMPT
        elif bug_layer == BugLayer.CROSS_LAYER:
            return CROSS_LAYER_FIX_SYSTEM_PROMPT
        elif bug_layer == BugLayer.MEDIATEK:
            # MediaTek bugs should be auto-escalated (no fix generation)
            logger.warning("MediaTek layer should be auto-escalated, not fixed by AI")
            return "ERROR: MediaTek bugs should not reach fix generation stage"
        else:
            logger.error(f"Unknown bug layer: {bug_layer}")
            return LOKI_FIX_SYSTEM_PROMPT  # Default fallback
    
    def _format_historical_fixes(self, historical_fixes: List[Dict]) -> str:
        """
        Format historical fixes with age warnings (temporal decay).
        
        Args:
            historical_fixes: List of similar fixes from Qdrant
            
        Returns:
            Markdown-formatted string with age warnings
        """
        if not historical_fixes:
            return "No historical fixes found for similar issues."
        
        lines = ["## Historical Fixes (Similar Issues)\n"]
        
        for i, fix in enumerate(historical_fixes[:5], 1):  # Top 5
            title = fix.get("title", "Unknown fix")
            pr_url = fix.get("pr_url", "")
            fix_date = fix.get("fix_date", "")
            score = fix.get("final_score", 0.0)
            fix_summary = fix.get("fix_summary", "")
            
            # Calculate age and add warning if >6 months
            age_warning = ""
            if fix_date:
                try:
                    fix_dt = datetime.fromisoformat(fix_date.replace("Z", "+00:00"))
                    age_days = (datetime.now(timezone.utc) - fix_dt).days
                    age_months = age_days / 30
                    
                    if age_months > 6:
                        age_warning = f" ⚠️ **{age_months:.1f} months old** (temporal decay)"
                except Exception as e:
                    logger.warning(f"Failed to parse fix date {fix_date}: {e}")
            
            lines.append(f"### {i}. {title} (score: {score:.2f}){age_warning}\n")
            if pr_url:
                lines.append(f"- **PR**: {pr_url}\n")
            if fix_summary:
                lines.append(f"- **Summary**: {fix_summary[:200]}...\n")
            lines.append("\n")
        
        return "".join(lines)
    
    def _format_mistakes(self, known_mistakes: List[Dict]) -> str:
        """
        Format known mistakes (anti-patterns to NEVER repeat).
        
        Args:
            known_mistakes: List of known anti-patterns from Qdrant
            
        Returns:
            Markdown-formatted string with anti-patterns
        """
        if not known_mistakes:
            return "No known mistakes found for this error category."
        
        lines = ["## Known Mistakes (NEVER Repeat These)\n\n"]
        lines.append("❌ **These patterns caused regressions. DO NOT use:**\n\n")
        
        for i, mistake in enumerate(known_mistakes[:3], 1):  # Top 3
            anti_pattern = mistake.get("anti_pattern", "")
            why_bad = mistake.get("why_bad", "")
            incident_count = mistake.get("incident_count", 0)
            
            lines.append(f"### {i}. {anti_pattern}\n")
            if why_bad:
                lines.append(f"- **Why Bad**: {why_bad}\n")
            if incident_count:
                lines.append(f"- **Regression Count**: {incident_count}x\n")
            lines.append("\n")
        
        return "".join(lines)
    
    def _format_repro_evidence(self, repro: Optional[ReproResultV2]) -> str:
        """
        Format reproduction evidence from Phase 9 (NEW in v6.0).
        
        Args:
            repro: ReproResultV2 from BugReproductionAgent (optional)
            
        Returns:
            Markdown-formatted string with reproduction evidence
        """
        if not repro or repro.status != ReproductionStatus.REPRODUCED:
            return ""
        
        lines = ["## Bug Reproduction Evidence (Captured on Dev TV)\n\n"]
        lines.append(f"✅ **Bug was successfully reproduced** (strategy: {repro.strategy.value})\n\n")
        
        # Companion info
        if repro.companion_info:
            lines.append("### Device Configuration\n")
            lines.append(f"- **Firmware**: {repro.companion_info.firmware_version}\n")
            lines.append(f"- **Chipset**: {repro.companion_info.chipset}\n")
            lines.append(f"- **LOKi Version**: {repro.companion_info.loki_version}\n")
            lines.append(f"- **Companion API**: {repro.companion_info.companion_api_version}\n")
            if repro.companion_info.chromium_version:
                lines.append(f"- **Chromium**: {repro.companion_info.chromium_version}\n")
            lines.append("\n")
        
        # Error logs (truncated)
        if repro.evidence and repro.evidence.logs:
            lines.append("### Captured Error Logs\n")
            lines.append("```\n")
            lines.append(repro.evidence.logs[:2000])  # First 2000 chars
            if len(repro.evidence.logs) > 2000:
                lines.append("\n... (truncated)")
            lines.append("\n```\n\n")
        
        # Baseline metrics
        if repro.baseline_metrics:
            lines.append("### Baseline Metrics (Pre-Fix)\n")
            if repro.baseline_metrics.loki_memory_mb:
                lines.append(f"- **LOKi Memory**: {repro.baseline_metrics.loki_memory_mb:.1f} MB\n")
            if repro.baseline_metrics.chromium_memory_mb:
                lines.append(f"- **Chromium Memory**: {repro.baseline_metrics.chromium_memory_mb:.1f} MB\n")
            if repro.baseline_metrics.cpu_percent:
                lines.append(f"- **CPU Usage**: {repro.baseline_metrics.cpu_percent:.1f}%\n")
            lines.append(f"- **Error Rate**: {repro.baseline_metrics.error_rate_per_min:.2f}/min\n")
            lines.append(f"- **Crash Count**: {repro.baseline_metrics.crash_count}\n")
            lines.append("\n")
        
        return "".join(lines)
    
    async def _generate_one(
        self,
        strategy: str,
        system_prompt: str,
        root_cause: RootCauseResult,
        context: ContextResult,
        historical: str,
        mistakes: str,
        repro: str,
        state: PipelineState,
    ) -> FixCandidate:
        """
        Generate a single fix candidate with specified strategy.
        
        Args:
            strategy: "SURGICAL", "DEFENSIVE", or "REFACTORED"
            system_prompt: Layer-specific system prompt
            root_cause: Root cause analysis
            context: ContextResult (code locations, similar fixes, mistakes)
            historical: Formatted historical fixes
            mistakes: Formatted known mistakes
            repro: Formatted reproduction evidence
            state: PipelineState
            
        Returns:
            FixCandidate with strategy, confidence, diff, explanation
        """
        logger.info(f"Generating {strategy} fix candidate...")
        
        # Build user prompt with all context
        user_prompt = self._build_user_prompt(
            strategy=strategy,
            root_cause=root_cause,
            context=context,
            historical=historical,
            mistakes=mistakes,
            repro=repro,
            state=state,
        )
        
        # Call Claude Opus 4
        try:
            response = await self.llm.generate(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                model="claude-opus-4",  # Highest capability for code generation
                temperature=0.3,  # Low temperature for deterministic code
                max_tokens=4000,
            )
            
            # Parse JSON response
            fix_data = self._parse_fix_response(response, strategy)
            
            # Generate summary from explanation or use strategy name
            summary = fix_data.get("summary", "")
            if not summary:
                # Extract first sentence from explanation as summary
                explanation = fix_data.get("explanation", "")
                if explanation:
                    first_sentence = explanation.split(".")[0].strip()
                    summary = first_sentence[:100] if len(first_sentence) > 100 else first_sentence
                else:
                    summary = f"Fix using {strategy} strategy"
            
            # Create FixCandidate object
            candidate = FixCandidate(
                strategy=self._map_strategy_to_enum(strategy),
                confidence=fix_data.get("confidence", 0.7),
                routing=self._determine_routing(fix_data.get("confidence", 0.7)),
                diff=fix_data.get("diff", ""),
                explanation=fix_data.get("explanation", ""),
                summary=summary,
                file_changes=fix_data.get("file_changes", []),
            )
            
            logger.info(f"{strategy} candidate generated (confidence: {candidate.confidence:.2f})")
            return candidate
            
        except Exception as e:
            logger.error(f"Failed to generate {strategy} candidate: {e}")
            # Return low-confidence placeholder
            return FixCandidate(
                strategy=self._map_strategy_to_enum(strategy),
                confidence=0.0,
                routing=ConfidenceRouting.ESCALATE_HUMAN,
                diff="",
                explanation=f"Fix generation failed: {str(e)}",
                summary=f"Fix generation failed for {strategy}",
            )
    
    def _build_user_prompt(
        self,
        strategy: str,
        root_cause: RootCauseResult,
        context: ContextResult,
        historical: str,
        mistakes: str,
        repro: str,
        state: PipelineState,
    ) -> str:
        """
        Build comprehensive user prompt with all context.
        
        Args:
            strategy: Fix strategy name
            root_cause: Root cause analysis
            context: RepoLocatorResult (code locations, similar fixes, mistakes)
            historical: Historical fixes
            mistakes: Known mistakes
            repro: Reproduction evidence
            state: PipelineState
            
        Returns:
            Complete user prompt string
        """
        lines = []
        
        # Strategy guidance
        lines.append(f"# Generate {strategy} Fix\n\n")
        lines.append(get_strategy_guidance(PromptFixStrategy[strategy]))
        lines.append("\n")
        
        # Ticket information
        lines.append("# Jira Ticket Information\n\n")
        lines.append(f"- **Ticket**: {state.ticket.key}\n")
        lines.append(f"- **Summary**: {state.ticket.summary}\n")
        lines.append(f"- **Description**: {state.ticket.description[:500]}...\n\n")
        
        # Root cause
        lines.append("# Root Cause Analysis\n\n")
        lines.append(f"- **Root Cause**: {root_cause.root_cause}\n")
        lines.append(f"- **Confidence**: {root_cause.confidence:.2f}\n")
        lines.append(f"- **Error Category**: {root_cause.error_category}\n")
        lines.append(f"- **Severity**: {root_cause.severity}\n")
        if root_cause.affected_files:
            lines.append(f"- **Affected Files**: {', '.join(root_cause.affected_files[:5])}\n")
        lines.append("\n")
        
        # Code context (top 3 locations)
        if context.primary_locations:
            lines.append("# Code Context (Top 3 Locations)\n\n")
            for i, loc in enumerate(context.primary_locations[:3], 1):
                # Handle both dict (from ContextBuilder) and CodeLocation (from tests)
                if isinstance(loc, dict):
                    file_path = loc.get("file_path", "unknown")
                    confidence = loc.get("confidence", 0.0)
                    content_preview = loc.get("content_preview", "")
                else:  # CodeLocation object
                    file_path = getattr(loc, "file_path", getattr(loc, "path", "unknown"))
                    confidence = getattr(loc, "confidence", 0.0)
                    content_preview = getattr(loc, "content_preview", "")
                
                lines.append(f"## {i}. {file_path} (confidence: {confidence:.2f})\n")
                if content_preview:
                    lines.append(f"```\n{content_preview}\n```\n")
                lines.append("\n")
        
        # Historical context
        lines.append(historical)
        lines.append("\n")
        
        # Known mistakes
        lines.append(mistakes)
        lines.append("\n")
        
        # Reproduction evidence (NEW in v6.0)
        if repro:
            lines.append(repro)
            lines.append("\n")
        
        # Final instruction
        lines.append("# Your Task\n\n")
        lines.append(f"Generate a {strategy} fix following the strategy guidance above. ")
        lines.append("Return a valid JSON object matching the output format in the system prompt. ")
        lines.append("Include all required fields: strategy, confidence, file_changes, diff, explanation.\n")
        
        return "".join(lines)
    
    def _parse_fix_response(self, response: str, strategy: str) -> Dict[str, Any]:
        """
        Parse LLM JSON response into fix data.
        
        Args:
            response: JSON string from Claude
            strategy: Strategy name for logging
            
        Returns:
            Parsed fix data dictionary
        """
        import json
        
        try:
            # Extract JSON from response (may have markdown code blocks)
            if "```json" in response:
                json_start = response.index("```json") + 7
                json_end = response.index("```", json_start)
                json_str = response[json_start:json_end].strip()
            elif "```" in response:
                json_start = response.index("```") + 3
                json_end = response.index("```", json_start)
                json_str = response[json_start:json_end].strip()
            else:
                json_str = response.strip()
            
            fix_data = json.loads(json_str)
            
            # Validate required fields
            required_fields = ["strategy", "confidence", "diff", "explanation"]
            for field in required_fields:
                if field not in fix_data:
                    logger.warning(f"{strategy}: Missing field '{field}' in response")
                    fix_data[field] = "" if field != "confidence" else 0.5
            
            return fix_data
            
        except json.JSONDecodeError as e:
            logger.error(f"{strategy}: JSON parse error: {e}")
            logger.debug(f"Response was: {response[:500]}...")
            return {
                "strategy": strategy,
                "confidence": 0.3,
                "diff": "",
                "explanation": f"Failed to parse LLM response: {str(e)}",
                "file_changes": [],
            }
        except Exception as e:
            logger.error(f"{strategy}: Unexpected error parsing response: {e}")
            return {
                "strategy": strategy,
                "confidence": 0.3,
                "diff": "",
                "explanation": f"Error: {str(e)}",
                "file_changes": [],
            }
    
    def _map_strategy_to_enum(self, strategy_name: str) -> FixStrategy:
        """
        Map strategy name to FixStrategy enum.
        
        Args:
            strategy_name: "SURGICAL", "DEFENSIVE", or "REFACTORED"
            
        Returns:
            FixStrategy enum value
        """
        # Simplified mapping for MVP
        mapping = {
            "SURGICAL": FixStrategy.NULL_CHECK,
            "DEFENSIVE": FixStrategy.MUTEX_GUARD,
            "REFACTORED": FixStrategy.SMART_POINTER,
        }
        
        return mapping.get(strategy_name, FixStrategy.UNKNOWN)
    
    def _determine_routing(self, confidence: float) -> ConfidenceRouting:
        """
        Determine PR routing based on confidence score.
        
        Args:
            confidence: Fix confidence (0.0-1.0)
            
        Returns:
            ConfidenceRouting enum value
        """
        if confidence >= 0.85:
            return ConfidenceRouting.AUTO_PR
        elif confidence >= 0.60:
            return ConfidenceRouting.PR_WITH_REVIEW
        elif confidence >= 0.40:
            return ConfidenceRouting.ANALYSIS_ONLY
        else:
            return ConfidenceRouting.ESCALATE_HUMAN
