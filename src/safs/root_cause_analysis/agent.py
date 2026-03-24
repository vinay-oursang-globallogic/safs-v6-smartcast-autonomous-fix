"""
SAFS v6.0 — Root Cause Analysis Agent

Two-phase root cause analysis:
1. Heuristic pre-filter: Use POC SmartTVErrorAnalyzer._infer_root_causes() results
2. LLM synthesis: Claude Haiku cross-references all evidence

The POC's heuristic engine provides ranked root cause candidates with
confidence scores based on:
- Pattern match frequency
- Temporal correlation strength  
- Cascading failure chain detection
- Context relevance to Jira description

The LLM then synthesizes across ALL evidence (heuristic candidates,
correlated errors, incidents, anomalies, symbolicated frames) to produce
a final root cause with confidence score and error category classification.

Usage:
    agent = RootCauseAgent(api_key=os.getenv("ANTHROPIC_API_KEY"))
    result = await agent.analyze(
        state=pipeline_state,
        log_analysis=log_intelligence_result,
    )
"""

import logging
from typing import Any, Optional

from safs.log_analysis.models import (
    BugLayer,
    ErrorCategory,
    MistakeSeverity,
    PipelineState,
    RootCauseResult,
)
from safs.log_intelligence.models import (
    LogAnalysisResult,
    LokiSymbolicationResult,
    CDPParseResult,
    MediaTekKernelResult,
)
from safs.root_cause_analysis.llm_client import LLMClient
from safs.root_cause_analysis.prompts import get_system_prompt

logger = logging.getLogger(__name__)


class RootCauseAgent:
    """
    Root Cause Analysis Agent — Stage 3 of SAFS pipeline.
    
    Two-phase approach:
    1. Heuristic pre-filter: Use SmartTVErrorAnalyzer heuristic root causes
    2. LLM synthesis: Claude Haiku synthesizes all evidence
    
    The agent builds a comprehensive evidence summary including:
    - Drain templates (95%+ log compression)
    - Heuristic root cause candidates
    - Temporal error correlations
    - Incidents (60s gap clustering)
    - Anomalies (3x baseline spikes)
    - Cascading failures
    - Layer-specific evidence:
      - LOKi: Symbolicated stack frames (ASLR-corrected)
      - HTML5: CDP exceptions + source-mapped frames
      - MediaTek: Kernel oops + subsystem classification
    
    The LLM uses layer-specific system prompts with:
    - Architecture context (LOKi/HTML5/MediaTek specifics)
    - Common bug patterns
    - Confidence calibration guidelines
    - Output format requirements
    """
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "claude-haiku",
        temperature: float = 0.0,
        max_retries: int = 3,
    ):
        """
        Initialize Root Cause Agent.
        
        Args:
            api_key: Anthropic API key (defaults to ANTHROPIC_API_KEY env var)
            model: LLM model name (claude-haiku for RCA, claude-opus for fix gen)
            temperature: Sampling temperature 0.0-1.0 (0.0 = deterministic)
            max_retries: Max retry attempts for LLM requests
        """
        self.llm = LLMClient(api_key=api_key, max_retries=max_retries)
        self.model = model
        self.temperature = temperature
        
        logger.info(f"RootCauseAgent initialized (model={model}, temperature={temperature})")
    
    async def analyze(
        self,
        state: PipelineState,
        log_analysis: LogAnalysisResult,
    ) -> RootCauseResult:
        """
        Perform two-phase root cause analysis.
        
        Phase 1: Heuristic pre-filter (already computed in log_analysis.heuristic_root_causes)
        Phase 2: LLM synthesis with layer-specific system prompt
        
        Args:
            state: Pipeline state with ticket info, bug layer, context keywords
            log_analysis: Log Intelligence Agent output with all analysis results
        
        Returns:
            RootCauseResult with root cause, confidence, error category, severity
        """
        logger.info(f"Starting root cause analysis for ticket {state.ticket.key}, layer={state.buglayer_result.layer.value}")
        
        # Phase 1: Extract heuristic candidates (already computed)
        heuristic_candidates = log_analysis.heuristic_root_causes
        logger.info(f"Heuristic pre-filter: {len(heuristic_candidates)} candidates")
        
        # Phase 2: Build evidence summary for LLM
        evidence_summary = self._build_evidence_summary(
            ticket=state.ticket,
            bug_layer=state.buglayer_result.layer,
            log_analysis=log_analysis,
            heuristic_candidates=heuristic_candidates,
        )
        
        # Select layer-specific system prompt
        system_prompt = get_system_prompt(state.buglayer_result.layer.value)
        
        # Call LLM for synthesis
        logger.debug(f"Calling LLM with evidence summary ({len(evidence_summary)} chars)")
        result = await self.llm.complete(
            system_prompt=system_prompt,
            user_prompt=evidence_summary,
            response_model=RootCauseResult,
            model=self.model,
            temperature=self.temperature,
        )
        
        logger.info(
            f"Root cause analysis complete: category={result.error_category.value}, "
            f"confidence={result.confidence:.2f}, severity={result.severity.value}"
        )
        
        return result
    
    def _build_evidence_summary(
        self,
        ticket: Any,
        bug_layer: BugLayer,
        log_analysis: LogAnalysisResult,
        heuristic_candidates: list,
    ) -> str:
        """
        Build comprehensive evidence summary for LLM synthesis.
        
        Includes:
        - Ticket context (key, summary, description)
        - Drain templates (top 10 by frequency)
        - Heuristic root cause candidates (top 5)
        - Temporal correlations (top 10)
        - Incidents (error bursts)
        - Anomalies (3x baseline spikes)
        - Cascading failures
        - Layer-specific evidence
        
        Args:
            ticket: Jira ticket object
            bug_layer: BugLayer classification
            log_analysis: LogAnalysisResult with all evidence
            heuristic_candidates: Heuristic root cause candidates
        
        Returns:
            Formatted evidence summary string (Markdown)
        """
        sections = []
        
        # Ticket context
        sections.append("# Ticket Context\n")
        sections.append(f"**Ticket**: {ticket.key}\n")
        sections.append(f"**Summary**: {ticket.summary}\n")
        sections.append(f"**Description**:\n{ticket.description[:500]}...\n")  # Truncate long descriptions
        sections.append(f"**Bug Layer**: {bug_layer.value}\n")
        
        # Drain templates (top 10)
        if log_analysis.drain and log_analysis.drain.templates:
            sections.append("\n# Log Templates (Drain Clustering)\n")
            sections.append(f"**Total logs**: {log_analysis.drain.total_logs}\n")
            sections.append(f"**Reduction ratio**: {log_analysis.drain.reduction_ratio:.1%}\n")
            sections.append("\n**Top 10 templates by frequency**:\n")
            for i, template in enumerate(log_analysis.drain.templates[:10], 1):
                sections.append(f"{i}. `{template.template}` (count={template.count})\n")
                if template.examples:
                    sections.append(f"   Example: {template.examples[0][:100]}...\n")
        
        # Heuristic root cause candidates (top 5)
        if heuristic_candidates:
            sections.append("\n# Heuristic Root Cause Candidates\n")
            for i, candidate in enumerate(heuristic_candidates[:5], 1):
                sections.append(f"{i}. {candidate}\n")
        
        # Temporal correlations (top 10)
        if log_analysis.correlations:
            sections.append("\n# Temporal Error Correlations\n")
            for i, corr in enumerate(log_analysis.correlations[:10], 1):
                sections.append(
                    f"{i}. `{corr.error1}` → `{corr.error2}` "
                    f"(count={corr.count}, avg_gap={corr.avg_time_diff_seconds:.1f}s, "
                    f"confidence={corr.confidence:.2f})\n"
                )
        
        # Incidents (60s gap clustering)
        if log_analysis.incidents:
            sections.append("\n# Incidents (Error Burst Clustering)\n")
            for i, incident in enumerate(log_analysis.incidents[:5], 1):
                sections.append(
                    f"{i}. **Incident {i}**: {len(incident.unique_error_types)} error types, "
                    f"{incident.error_count} total errors, "
                    f"duration={incident.duration_seconds:.1f}s\n"
                )
                sections.append(f"   - Start: {incident.start_time}\n")
                error_types_list = list(incident.unique_error_types)[:5]
                sections.append(f"   - Error types: {', '.join(error_types_list)}\n")
        
        # Anomalies (3x baseline spikes)
        if log_analysis.anomalies:
            sections.append("\n# Anomalies (Error Rate Spikes)\n")
            for i, anomaly in enumerate(log_analysis.anomalies[:5], 1):
                sections.append(
                    f"{i}. `{anomaly.error_type}`: spike from {anomaly.baseline_rate:.1f}/min "
                    f"to {anomaly.spike_rate:.1f}/min ({anomaly.severity})\n"
                )
        
        # Cascading failures
        if log_analysis.cascading_failures:
            sections.append("\n# Cascading Failures\n")
            for i, cascade in enumerate(log_analysis.cascading_failures[:3], 1):
                error_chain = " → ".join(cascade.chain)
                sections.append(f"{i}. **{cascade.impact}**: {error_chain}\n")
                sections.append(f"   - Total duration: {cascade.total_duration_seconds:.1f}s\n")
        
        # Layer-specific evidence
        sections.append(f"\n# Layer-Specific Evidence ({bug_layer.value})\n")
        
        if bug_layer == BugLayer.LOKI and log_analysis.loki_symbolication:
            sections.append(self._format_loki_evidence(log_analysis.loki_symbolication))
        
        elif bug_layer == BugLayer.HTML5 and log_analysis.cdp_analysis:
            sections.append(self._format_html5_evidence(
                log_analysis.cdp_analysis,
                log_analysis.source_mapped_frames,
            ))
        
        elif bug_layer == BugLayer.MEDIATEK and log_analysis.mediatek_analysis:
            sections.append(self._format_mediatek_evidence(log_analysis.mediatek_analysis))
        
        elif bug_layer == BugLayer.CROSS_LAYER:
            if log_analysis.loki_symbolication:
                sections.append("## LOKi Evidence\n")
                sections.append(self._format_loki_evidence(log_analysis.loki_symbolication))
            if log_analysis.cdp_analysis:
                sections.append("\n## HTML5 Evidence\n")
                sections.append(self._format_html5_evidence(
                    log_analysis.cdp_analysis,
                    log_analysis.source_mapped_frames,
                ))
        
        return "\n".join(sections)
    
    def _format_loki_evidence(self, loki: LokiSymbolicationResult) -> str:
        """Format LOKi symbolication evidence."""
        lines = []
        
        lines.append(f"**Symbolication success rate**: {loki.symbolication_success_rate:.1%}\n")
        lines.append(f"**Total frames**: {len(loki.symbolicated_frames)}\n")
        
        if loki.symbolicated_frames:
            lines.append("\n**Symbolicated Stack Frames** (top 10):\n")
            for i, frame in enumerate(loki.symbolicated_frames[:10], 1):
                if frame.status == "OK":
                    lines.append(
                        f"{i}. `{frame.function_name}` at `{frame.file_name}:{frame.line_number}` "
                        f"(library={frame.library_name}, offset=0x{frame.file_offset:x})\n"
                    )
                else:
                    lines.append(
                        f"{i}. (Unsymbolicated) library={frame.library_name}, "
                        f"PC=0x{frame.virtual_pc:x}, status={frame.status}\n"
                    )
        
        return "".join(lines)
    
    def _format_html5_evidence(
        self,
        cdp: CDPParseResult,
        source_mapped_frames: Optional[list],
    ) -> str:
        """Format HTML5 CDP + source map evidence."""
        lines = []
        
        lines.append(f"**CDP events parsed**: {len(cdp.events)}\n")
        lines.append(f"**Exceptions**: {len(cdp.exceptions)}\n")
        lines.append(f"**Console errors**: {len(cdp.console_errors)}\n")
        lines.append(f"**Network errors**: {len(cdp.network_errors)}\n")
        
        if cdp.exceptions:
            lines.append("\n**JavaScript Exceptions** (top 5):\n")
            for i, exc in enumerate(cdp.exceptions[:5], 1):
                lines.append(
                    f"{i}. `{exc.exception_type}`: {exc.message}\n"
                    f"   - Location: {exc.url}:{exc.line_number}:{exc.column_number}\n"
                )
                if exc.stack_trace:
                    lines.append(f"   - Stack trace: {len(exc.stack_trace)} frames\n")
        
        if source_mapped_frames:
            lines.append("\n**Source-Mapped Frames** (top 10):\n")
            for i, frame in enumerate(source_mapped_frames[:10], 1):
                if frame.status == "OK" and frame.original_position:
                    lines.append(
                        f"{i}. `{frame.original_position.original_file}:{frame.original_position.original_line}:{frame.original_position.original_column}` "
                        f"(minified: {frame.minified_file}:{frame.minified_line}:{frame.minified_column})\n"
                    )
                else:
                    lines.append(
                        f"{i}. (Unmapped) {frame.minified_file}:{frame.minified_line}, "
                        f"status={frame.status}\n"
                    )
        
        return "".join(lines)
    
    def _format_mediatek_evidence(self, mediatek: MediaTekKernelResult) -> str:
        """Format MediaTek kernel oops evidence."""
        lines = []
        
        lines.append(f"**Kernel oopses detected**: {len(mediatek.oops_list)}\n")
        lines.append(f"**Hardware errors detected**: {len(mediatek.hardware_errors)}\n")
        
        if mediatek.subsystem_classification:
            lines.append("\n**Subsystem Classification**:\n")
            for subsystem, count in mediatek.subsystem_classification.items():
                lines.append(f"- {subsystem}: {count} oops\n")
        
        if mediatek.oops_list:
            lines.append("\n**Kernel Oopses** (top 5):\n")
            for i, oops in enumerate(mediatek.oops_list[:5], 1):
                lines.append(
                    f"{i}. **{oops.oops_type}** in subsystem `{oops.subsystem}`\n"
                    f"   - Faulting address: 0x{oops.faulting_address:x}\n"
                    f"   - Instruction pointer: 0x{oops.instruction_pointer:x}\n"
                )
                if oops.call_trace:
                    lines.append(f"   - Call trace: {len(oops.call_trace)} frames\n")
                    for trace_line in oops.call_trace[:3]:
                        lines.append(f"     - {trace_line}\n")
        
        if mediatek.hardware_errors:
            lines.append("\n**Hardware Errors (Auto-Escalate to hw_triage)**:\n")
            for error in mediatek.hardware_errors[:5]:
                lines.append(f"- {error}\n")
        
        return "".join(lines)
    
    async def close(self):
        """Close LLM client."""
        await self.llm.close()
