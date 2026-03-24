"""
Bug Reproduction Agent
======================

Stage 5.5: Bug Reproduction (NEW in v6.0)

Attempts to reproduce the bug on a dev TV before fix generation.
Powered by vizio-mcp MCP servers (vizio-remote, vizio-ssh, vizio-loki).

Master Prompt Reference: Section 3.8 - Stage 5.5

Benefits:
1. Prevents wasted effort on non-reproducible issues
2. Captures baseline evidence for before/after comparison
3. Establishes ground-truth metrics for validation
4. Increases confidence when bug IS reproduced
"""

import logging
import asyncio
import re
import time
from typing import Optional, List, Dict, Any

from safs.log_analysis.models import (
    PipelineState,
    BugLayer,
    ErrorCategory,
)
from .models import (
    ReproResultV2 as ReproResult,
    CompanionLibInfo,
    ReproductionStrategy,
    ReproductionStatus,
    ReproductionEvidence,
    BaselineMetrics,
    ReproStep,
)
from .device_resolver import DynamicCompanionLibResolver

logger = logging.getLogger(__name__)


class BugReproductionAgent:
    """
    Bug Reproduction Agent for Stage 5.5.
    
    Attempts to reproduce the reported bug on a dev TV before fix generation.
    Uses vizio-mcp MCP servers for TV control and monitoring.
    
    Usage:
        agent = BugReproductionAgent(
            remote_mcp=vizio_remote_client,
            ssh_mcp=vizio_ssh_client,
            loki_mcp=vizio_loki_client,
            tv_available=True,
        )
        
        result = await agent.attempt(pipeline_state)
        
        if result.status == ReproductionStatus.REPRODUCED:
            # Proceed with fix generation with evidence
            fix = await fix_generator.generate(context, result.evidence)
        elif result.status == ReproductionStatus.NOT_REPRODUCED:
            # Reduce confidence, may skip fix generation
            confidence *= 0.7
    """
    
    def __init__(
        self,
        remote_mcp=None,
        ssh_mcp=None,
        loki_mcp=None,
        tv_available: bool = False,
    ):
        """
        Initialize Bug Reproduction Agent.
        
        Args:
            remote_mcp: vizio-remote MCP client (SCPL control)
            ssh_mcp: vizio-ssh MCP client (SSH + registry)
            loki_mcp: vizio-loki MCP client (Loki TCP:4242)
            tv_available: Whether dev TV is available and configured
        """
        self.remote = remote_mcp
        self.ssh = ssh_mcp
        self.loki = loki_mcp
        self.tv_available = tv_available
        
        if tv_available and (not ssh_mcp or not remote_mcp):
            logger.warning(
                "TV marked as available but MCP clients not provided. "
                "Reproduction will be skipped."
            )
            self.tv_available = False
    
    async def attempt(self, state: PipelineState) -> ReproResult:
        """
        Attempt to reproduce the bug on dev TV.
        
        Args:
            state: PipelineState with ticket, bug layer, error category
            
        Returns:
            ReproResult with status REPRODUCED/NOT_REPRODUCED/SKIP
        """
        start_time = time.time()
        
        # Check if TV is available
        if not self.tv_available:
            logger.info("Dev TV not available, skipping reproduction")
            return self._build_skip_result(
                reason="No dev TV available",
                execution_time=time.time() - start_time,
            )
        
        try:
            # 1. Check firmware compatibility
            companion_info = await self._resolve_companion_info()
            
            ticket_firmware = getattr(state.ticket, "firmware_version", None)
            
            resolver = DynamicCompanionLibResolver(self.ssh)
            if not resolver.check_firmware_compatible(
                companion_info.firmware_version, ticket_firmware
            ):
                logger.warning(
                    f"Firmware mismatch: TV={companion_info.firmware_version}, "
                    f"Ticket={ticket_firmware}"
                )
                return self._build_skip_result(
                    reason=f"Firmware mismatch (TV: {companion_info.firmware_version}, "
                           f"Ticket: {ticket_firmware})",
                    companion_info=companion_info,
                    execution_time=time.time() - start_time,
                )
            
            # 2. Determine reproduction strategy
            strategy, repro_steps = self._determine_strategy(state)
            
            # 3. Execute reproduction steps
            logger.info(f"Executing reproduction with strategy: {strategy}")
            await self._execute_reproduction(state, repro_steps)
            
            # 4. Capture evidence
            evidence = await self._capture_evidence(state)
            
            # 5. Capture baseline metrics
            baseline = await self._capture_baseline_metrics()
            
            # 6. Check if error manifests
            reproduced = self._check_error_present(
                evidence.logs,
                state.buglayer_result.matched_patterns if state.buglayer_result else [],
            )
            
            execution_time = time.time() - start_time
            
            status = (
                ReproductionStatus.REPRODUCED
                if reproduced
                else ReproductionStatus.NOT_REPRODUCED
            )
            
            logger.info(
                f"Reproduction {status.value} in {execution_time:.1f}s. "
                f"Errors found: {evidence.error_count}"
            )
            
            return ReproResult(
                status=status,
                strategy=strategy,
                evidence=evidence,
                companion_info=companion_info,
                baseline_metrics=baseline,
                repro_steps_executed=repro_steps,
                execution_time_seconds=execution_time,
            )
            
        except Exception as e:
            logger.error(f"Reproduction attempt failed: {e}", exc_info=True)
            return self._build_skip_result(
                reason=f"Reproduction error: {str(e)}",
                execution_time=time.time() - start_time,
            )
    
    async def _resolve_companion_info(self) -> CompanionLibInfo:
        """
        Resolve companion library info from live TV registry.
        
        Returns:
            CompanionLibInfo with live system information
        """
        resolver = DynamicCompanionLibResolver(self.ssh)
        return await resolver.resolve()
    
    def _determine_strategy(
        self, state: PipelineState
    ) -> tuple[ReproductionStrategy, List[ReproStep]]:
        """
        Determine reproduction strategy based on ticket information.
        
        Args:
            state: PipelineState with ticket info
            
        Returns:
            Tuple of (strategy, repro_steps)
        """
        # Check if ticket has explicit reproduction steps
        ticket_repro_steps = getattr(state.ticket, "repro_steps", None) or getattr(
            state.ticket, "reproduction_steps", None
        )
        
        if ticket_repro_steps and len(ticket_repro_steps) > 0:
            # DETERMINISTIC: Follow explicit steps
            repro_steps = self._parse_repro_steps(ticket_repro_steps)
            return ReproductionStrategy.DETERMINISTIC, repro_steps
        
        # EXPLORATORY: Launch app and wait for error
        streaming_app = getattr(state.ticket, "streaming_app", None) or getattr(
            state.ticket, "affected_app", None
        )
        
        if streaming_app:
            repro_steps = [
                ReproStep(
                    action="launch_app",
                    params={"app_name": streaming_app},
                    description=f"Launch {streaming_app}",
                ),
                ReproStep(
                    action="wait",
                    params={"seconds": 10},
                    description="Wait for app to load and error to manifest",
                ),
            ]
            return ReproductionStrategy.EXPLORATORY, repro_steps
        
        # No app info, just wait
        repro_steps = [
            ReproStep(
                action="wait",
                params={"seconds": 5},
                description="Wait for error to manifest",
            )
        ]
        return ReproductionStrategy.EXPLORATORY, repro_steps
    
    def _parse_repro_steps(self, raw_steps: List[str]) -> List[ReproStep]:
        """
        Parse raw reproduction steps from Jira ticket into ReproStep objects.
        
        Args:
            raw_steps: List of step strings (e.g., ["Launch Netflix", "Press Down key"])
            
        Returns:
            List of ReproStep objects
        """
        repro_steps = []
        
        for step_text in raw_steps:
            step_text = step_text.strip()
            
            # Try to parse structured steps
            if "launch" in step_text.lower():
                # Extract app name
                app_match = re.search(
                    r"launch\s+(\w+)", step_text, re.IGNORECASE
                )
                if app_match:
                    app_name = app_match.group(1)
                    repro_steps.append(
                        ReproStep(
                            action="launch_app",
                            params={"app_name": app_name},
                            description=step_text,
                        )
                    )
                    continue
            
            if "press" in step_text.lower() or "key" in step_text.lower():
                # Extract key name
                key_match = re.search(
                    r"(?:press|key)\s+(\w+)", step_text, re.IGNORECASE
                )
                if key_match:
                    key_name = key_match.group(1).capitalize()
                    repro_steps.append(
                        ReproStep(
                            action="send_key",
                            params={"key": key_name},
                            description=step_text,
                        )
                    )
                    continue
            
            if "wait" in step_text.lower():
                # Extract wait duration
                wait_match = re.search(r"(\d+)\s*(?:sec|second)", step_text, re.IGNORECASE)
                seconds = int(wait_match.group(1)) if wait_match else 5
                repro_steps.append(
                    ReproStep(
                        action="wait",
                        params={"seconds": seconds},
                        description=step_text,
                    )
                )
                continue
            
            # Generic step - defaults to wait
            repro_steps.append(
                ReproStep(
                    action="wait",
                    params={"seconds": 2},
                    description=step_text,
                )
            )
        
        return repro_steps
    
    async def _execute_reproduction(
        self, state: PipelineState, repro_steps: List[ReproStep]
    ) -> None:
        """
        Execute reproduction steps on dev TV.
        
        Args:
            state: PipelineState
            repro_steps: List of ReproStep to execute
        """
        for i, step in enumerate(repro_steps, 1):
            logger.info(f"Step {i}/{len(repro_steps)}: {step.description or step.action}")
            
            try:
                if step.action == "launch_app":
                    await self._execute_launch_app(step.params)
                elif step.action == "send_key":
                    await self._execute_send_key(step.params)
                elif step.action == "wait":
                    await self._execute_wait(step.params)
                else:
                    logger.warning(f"Unknown action: {step.action}, skipping")
            
            except Exception as e:
                logger.error(f"Failed to execute step {i}: {e}")
                # Continue with other steps
    
    async def _execute_launch_app(self, params: Dict[str, Any]) -> None:
        """Execute launch_app action via vizio-remote MCP."""
        app_name = params.get("app_name")
        
        if not app_name:
            raise ValueError("launch_app requires 'app_name' parameter")
        
        await self.remote.call("launch_app", app_name=app_name)
        await asyncio.sleep(8)  # Wait for app to launch
    
    async def _execute_send_key(self, params: Dict[str, Any]) -> None:
        """Execute send_key action via vizio-remote MCP."""
        key = params.get("key")
        
        if not key:
            raise ValueError("send_key requires 'key' parameter")
        
        await self.remote.call("send_key_press", key=key)
        await asyncio.sleep(1)  # Wait for key processing
    
    async def _execute_wait(self, params: Dict[str, Any]) -> None:
        """Execute wait action."""
        seconds = params.get("seconds", 1)
        await asyncio.sleep(seconds)
    
    async def _capture_evidence(self, state: PipelineState) -> ReproductionEvidence:
        """
        Capture evidence from dev TV after reproduction.
        
        Args:
            state: PipelineState with bug layer info
            
        Returns:
            ReproductionEvidence with logs, screenshot, scene graph
        """
        # Determine log unit based on bug layer
        log_unit = self._unit_for_layer(state.buglayer_result.layer)
        
        # Capture logs
        logs_result = await self.ssh.call(
            "get_logs",
            unit=log_unit,
            priority="err",
            since="5 minutes ago",
            lines=100,
        )
        
        logs = (
            logs_result if isinstance(logs_result, str)
            else logs_result.get("logs", "") if isinstance(logs_result, dict)
            else ""
        )
        
        # Capture screenshot (if loki MCP available)
        screenshot = None
        scene_graph = None
        
        if self.loki:
            try:
                screenshot = await self.loki.call("take_screenshot")
                scene_graph = await self.loki.call("get_scene_graph")
            except Exception as e:
                logger.warning(f"Failed to capture LOKi evidence: {e}")
        
        # Count errors matching patterns
        matched_patterns = state.buglayer_result.matched_patterns if state.buglayer_result else []
        error_count = self._count_errors_in_logs(logs, matched_patterns)
        
        return ReproductionEvidence(
            logs=logs,
            screenshot=screenshot,
            scene_graph=scene_graph,
            error_count=error_count,
            matched_patterns=[p for p in matched_patterns if p in logs],
        )
    
    def _unit_for_layer(self, bug_layer: BugLayer) -> str:
        """
        Determine systemd unit name for log capture based on bug layer.
        
        Args:
            bug_layer: Bug layer (LOKI, HTML5, MEDIATEK, etc.)
            
        Returns:
            Systemd unit name for logs
        """
        unit_map = {
            BugLayer.LOKI: "loki",
            BugLayer.HTML5: "cobalt",  # Chromium/Cobalt browser
            BugLayer.MEDIATEK: "kernel",  # Kernel logs (dmesg)
            BugLayer.CROSS_LAYER: "loki",  # Default to LOKi for cross-layer
            BugLayer.UNKNOWN: "loki",
        }
        
        return unit_map.get(bug_layer, "loki")
    
    async def _capture_baseline_metrics(self) -> BaselineMetrics:
        """
        Capture baseline system metrics from dev TV.
        
        Returns:
            BaselineMetrics with memory, CPU, error rate
        """
        try:
            # Get process memory usage
            loki_mem_cmd = "ps aux | grep '[l]oki' | awk '{print $6}'"
            chromium_mem_cmd = "ps aux | grep '[c]obalt' | awk '{print $6}'"
            
            loki_mem_result = await self.ssh.call("run_command", command=loki_mem_cmd)
            chromium_mem_result = await self.ssh.call("run_command", command=chromium_mem_cmd)
            
            loki_mem_kb = int(loki_mem_result.strip()) if loki_mem_result.strip().isdigit() else 0
            chromium_mem_kb = int(chromium_mem_result.strip()) if chromium_mem_result.strip().isdigit() else 0
            
            # Get system CPU usage
            cpu_cmd = "top -bn1 | grep 'Cpu(s)' | awk '{print $2}' | sed 's/%us//'"
            cpu_result = await self.ssh.call("run_command", command=cpu_cmd)
            cpu_percent = float(cpu_result.strip()) if cpu_result and cpu_result.strip().replace('.', '').isdigit() else 0.0
            
            return BaselineMetrics(
                loki_memory_mb=loki_mem_kb / 1024,
                chromium_memory_mb=chromium_mem_kb / 1024,
                cpu_percent=cpu_percent,
                error_rate_per_min=0.0,  # Would need historical data
                crash_count=0,  # Would need crash log analysis
            )
            
        except Exception as e:
            logger.warning(f"Failed to capture baseline metrics: {e}")
            return BaselineMetrics()
    
    def _check_error_present(
        self, logs: str, matched_patterns: List[str]
    ) -> bool:
        """
        Check if target error is present in captured logs.
        
        Args:
            logs: Captured log output
            matched_patterns: List of error pattern IDs from BugLayerRouter
            
        Returns:
            True if error found, False otherwise
        """
        if not logs:
            return False
        
        # If we have matched patterns from router, check for those
        if matched_patterns:
            for pattern in matched_patterns:
                # Pattern IDs are often descriptive strings
                if pattern.lower() in logs.lower():
                    return True
        
        # Fallback: check for common error indicators
        error_indicators = [
            "error",
            "exception",
            "fatal",
            "segfault",
            "sigsegv",
            "sigabrt",
            "assert",
            "panic",
            "crash",
        ]
        
        logs_lower = logs.lower()
        for indicator in error_indicators:
            if indicator in logs_lower:
                return True
        
        return False
    
    def _count_errors_in_logs(
        self, logs: str, matched_patterns: List[str]
    ) -> int:
        """
        Count number of error occurrences in logs.
        
        Args:
            logs: Captured log output
            matched_patterns: Error patterns from BugLayerRouter
            
        Returns:
            Error count
        """
        count = 0
        
        for pattern in matched_patterns:
            count += logs.lower().count(pattern.lower())
        
        # Also count generic error indicators
        count += logs.lower().count("error")
        count += logs.lower().count("exception")
        count += logs.lower().count("fatal")
        
        return count
    
    def _build_skip_result(
        self,
        reason: str,
        companion_info: Optional[CompanionLibInfo] = None,
        execution_time: float = 0.0,
    ) -> ReproResult:
        """
        Build a SKIP result.
        
        Args:
            reason: Reason for skipping
            companion_info: Optional companion info if resolved
            execution_time: Execution time in seconds
            
        Returns:
            ReproResult with SKIP status
        """
        return ReproResult(
            status=ReproductionStatus.SKIP,
            strategy=ReproductionStrategy.SKIP,
            reason=reason,
            companion_info=companion_info,
            execution_time_seconds=execution_time,
        )


__all__ = ["BugReproductionAgent"]
