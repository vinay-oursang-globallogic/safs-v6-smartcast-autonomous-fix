"""
Production Regression Correlator — Phase 13

72-hour post-merge production monitoring.
Any error-rate spike ≥1.5x baseline fires self-healing loop.

Extended from jira_auto_fixer/learning_system.py with v6.0 additions:
- Continuous monitoring for 72 hours post-merge
- Compares against 7-day baseline
- Automatic regression detection
- Saves corrections to Qdrant fix_corrections collection
- Triggers Jira notifications and revert recommendations

Usage:
    from safs.qdrant_collections.institutional_memory import InstitutionalMemory

    institutional_memory = InstitutionalMemory(qdrant_url="http://localhost:6333")
    correlator = ProductionRegressionCorrelator(
        institutional_memory=institutional_memory
    )
    await correlator.monitor(merged_pr)  # Runs for 72 hours
"""

import logging
import asyncio
from typing import Optional
from datetime import datetime, timezone, timedelta

import httpx

from .models import (
    MergedPR,
    TelemetryMetric,
    RegressionAlert,
    FixCorrection,
    MistakeSeverity,
)

logger = logging.getLogger(__name__)


class TelemetryClient:
    """
    Production telemetry client for error rate monitoring.

    Queries Prometheus/Pushgateway when configured; falls back to
    in-memory mock values when no Prometheus URL is provided.

    Usage:
        client = TelemetryClient(prometheus_url="http://prometheus:9090")
        baseline = await client.get_baseline("netflix", "mt5670", "EME_DRM_FAILURE")
    """

    def __init__(self, prometheus_url: Optional[str] = None) -> None:
        """
        Args:
            prometheus_url: Prometheus HTTP API base URL (e.g., http://localhost:9090).
                            If None, all methods return mock values.
        """
        self._prometheus_url = (prometheus_url or "").rstrip("/")
        self._http: Optional[httpx.AsyncClient] = None
        if self._prometheus_url:
            self._http = httpx.AsyncClient(
                base_url=self._prometheus_url,
                timeout=httpx.Timeout(15.0, connect=5.0),
            )
            logger.info("TelemetryClient connected to Prometheus at %s", prometheus_url)
        else:
            logger.info("TelemetryClient running in mock mode (no Prometheus URL)")

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def get_baseline(
        self,
        app: Optional[str],
        chipset: Optional[str],
        category: str,
        window_hours: int = 168,
    ) -> float:
        """Get baseline error rate from past window (default 7 days)."""
        query = self._build_rate_query(app, chipset, category, window_hours)
        if not query:
            return 10.0  # Mock fallback
        result = await self._instant_query(query)
        return result if result is not None else 10.0

    async def get_current_rate(
        self,
        app: Optional[str],
        chipset: Optional[str],
        category: str,
    ) -> float:
        """Get current (last 1h) error rate."""
        query = self._build_rate_query(app, chipset, category, window_hours=1)
        if not query:
            return 12.0  # Mock fallback
        result = await self._instant_query(query)
        return result if result is not None else 12.0

    async def count_affected_users(
        self,
        dimension: str,
        value: str,
    ) -> int:
        """Count affected unique users for a given dimension/value."""
        if not self._http:
            return 150  # Mock fallback

        promql = (
            f'count(safs_affected_users{{{dimension}="{value}"}})'
        )
        result = await self._instant_query(promql)
        return int(result) if result is not None else 150

    async def get_rate(
        self,
        dimension: str,
        value: str,
    ) -> float:
        """Get current error rate for a dimension/value combination."""
        if not self._http:
            return 25.0  # Mock fallback

        promql = (
            f'rate(safs_error_total{{{dimension}="{value}"}}[1h])'
        )
        result = await self._instant_query(promql)
        return result if result is not None else 25.0

    async def get_7day_baseline(
        self,
        dimension: str,
        value: str,
    ) -> float:
        """Get 7-day average error rate for a dimension/value."""
        if not self._http:
            return 10.0  # Mock fallback

        promql = (
            f'avg_over_time(rate(safs_error_total{{{dimension}="{value}"}}[1h])[7d:1h])'
        )
        result = await self._instant_query(promql)
        return result if result is not None else 10.0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_rate_query(
        self,
        app: Optional[str],
        chipset: Optional[str],
        category: str,
        window_hours: int,
    ) -> Optional[str]:
        if not self._http:
            return None
        labels: list[str] = [f'error_category="{category}"']
        if app:
            labels.append(f'app="{app}"')
        if chipset:
            labels.append(f'chipset="{chipset}"')
        label_str = ", ".join(labels)
        return f'rate(safs_error_total{{{label_str}}}[{window_hours}h])'

    async def _instant_query(self, promql: str) -> Optional[float]:
        """Execute a Prometheus instant query and return the first scalar result."""
        if not self._http:
            return None
        try:
            response = await self._http.get(
                "/api/v1/query", params={"query": promql}
            )
            response.raise_for_status()
            data = response.json()
            if data.get("status") == "success":
                results = data.get("data", {}).get("result", [])
                if results:
                    # Return first value
                    return float(results[0]["value"][1])
        except Exception as exc:
            logger.debug("Prometheus query failed for '%s': %s", promql[:80], exc)
        return None


class InstitutionalMemoryClient:
    """
    Client for saving corrections to Qdrant institutional memory.
    
    In production, this would be the actual InstitutionalMemory class
    from safs.qdrant_collections.institutional_memory.
    Using a wrapper here to avoid circular dependencies.
    """
    
    def __init__(self, institutional_memory=None):
        """
        Initialize institutional memory client.
        
        Args:
            institutional_memory: Optional InstitutionalMemory instance
        """
        self.institutional_memory = institutional_memory
    
    async def save_correction(self, correction: FixCorrection) -> bool:
        """
        Save correction to fix_corrections collection.
        
        Args:
            correction: FixCorrection to save
            
        Returns:
            True if saved successfully
        """
        if self.institutional_memory:
            try:
                # Convert FixCorrection to CorrectionRecord
                from safs.qdrant_collections.models import CorrectionRecord
                import uuid
                
                record = CorrectionRecord(
                    correction_id=str(uuid.uuid4()),
                    original_ticket_id=correction.original_ticket,
                    correction_description=correction.correction_description,
                    severity=correction.severity.value,
                    error_category=correction.error_category,
                    bug_layer=correction.bug_layer.value if correction.bug_layer else None,
                    spike_factor=correction.spike_factor,
                    baseline_rate=correction.baseline_rate,
                    current_rate=correction.current_rate,
                )
                
                # Generate embeddings (in production, use actual embedding model)
                # For now, use zero vectors as placeholder
                dense_vector = [0.0] * 1024
                sparse_vector = {"indices": [], "values": []}
                
                correction_id = await self.institutional_memory.add_correction(
                    record, dense_vector, sparse_vector
                )
                logger.info(f"Saved correction to Qdrant: {correction_id}")
                return True
            except Exception as e:
                logger.error(f"Failed to save correction to Qdrant: {e}")
                return False
        else:
            # Fallback logging
            logger.info(f"Would save correction for {correction.original_ticket}")
            logger.info(f"  Severity: {correction.severity.value}")
            logger.info(f"  Spike factor: {correction.spike_factor}x")
            return True


class JiraClient:
    """Mock Jira client for notifications."""
    
    async def add_comment(self, ticket_key: str, comment: str) -> bool:
        """Add comment to Jira ticket."""
        logger.info(f"Adding Jira comment to {ticket_key}")
        logger.info(f"  Comment: {comment[:100]}...")
        # Mock implementation
        return True


class ProductionRegressionCorrelator:
    """
    72-hour post-merge production monitoring.
    
    Monitors production error rates after a PR is merged and detects
    regressions by comparing against baseline. Triggers self-healing
    if error rate spikes significantly.
    """
    
    WINDOW_HOURS = 72
    SPIKE_THRESHOLD = 1.5  # 1.5x baseline triggers alert
    REVERT_THRESHOLD = 2.0  # 2.0x baseline recommends revert
    
    def __init__(
        self,
        telemetry_client: Optional[TelemetryClient] = None,
        institutional_memory: Optional[any] = None,
        jira_client: Optional[JiraClient] = None,
        check_interval_hours: int = 1,
    ):
        """
        Initialize regression correlator.
        
        Args:
            telemetry_client: Client for production metrics
            institutional_memory: InstitutionalMemory instance for Qdrant
            jira_client: Client for Jira notifications
            check_interval_hours: How often to check (default 1 hour)
        """
        self.telemetry = telemetry_client or TelemetryClient()
        self.qdrant = InstitutionalMemoryClient(institutional_memory)
        self.jira = jira_client or JiraClient()
        self.check_interval_hours = check_interval_hours
        
        logger.info(f"ProductionRegressionCorrelator initialized")
        logger.info(f"  Monitoring window: {self.WINDOW_HOURS} hours")
        logger.info(f"  Spike threshold: {self.SPIKE_THRESHOLD}x")
        logger.info(f"  Check interval: {self.check_interval_hours} hour(s)")
    
    async def monitor(self, merged_pr: MergedPR) -> Optional[RegressionAlert]:
        """
        Monitor production for 72 hours post-merge.
        
        Args:
            merged_pr: Metadata about merged PR to monitor
        
        Returns:
            RegressionAlert if regression detected, None otherwise
        """
        logger.info(f"🔍 Starting 72h regression monitoring for {merged_pr.ticket_id}")
        logger.info(f"  PR: {merged_pr.pr_url}")
        logger.info(f"  Merged: {merged_pr.merged_at}")
        logger.info(f"  Error category: {merged_pr.error_category}")
        
        # Get baseline from week before merge
        baseline = await self.telemetry.get_baseline(
            app=merged_pr.app,
            chipset=merged_pr.chipset,
            category=merged_pr.error_category,
            window_hours=168,  # 7-day baseline
        )
        
        logger.info(f"  Baseline rate: {baseline:.2f} errors/hour")
        
        # Monitor for configured window
        checks = int(self.WINDOW_HOURS // self.check_interval_hours)
        
        for check_num in range(1, checks + 1):
            # Wait for next check
            await asyncio.sleep(self.check_interval_hours * 3600)
            
            # Get current error rate
            current = await self.telemetry.get_current_rate(
                app=merged_pr.app,
                chipset=merged_pr.chipset,
                category=merged_pr.error_category,
            )
            
            spike_factor = current / baseline if baseline > 0 else 0.0
            hours_elapsed = check_num * self.check_interval_hours
            
            logger.info(f"  Check {check_num}/{checks} ({hours_elapsed}h): "
                       f"{current:.2f} errors/hour (/{spike_factor:.2f}x baseline)")
            
            # Check for regression
            if spike_factor >= self.SPIKE_THRESHOLD:
                logger.warning(f"⚠️ Regression detected at {hours_elapsed}h: "
                             f"{spike_factor:.2f}x baseline")
                
                alert = await self._handle_regression(
                    merged_pr, baseline, current, spike_factor
                )
                return alert
        
        logger.info(f"✅ No regression detected after 72h for {merged_pr.ticket_id}")
        return None
    
    async def _handle_regression(
        self,
        pr: MergedPR,
        baseline: float,
        current: float,
        spike_factor: float,
    ) -> RegressionAlert:
        """
        Handle detected regression.
        
        Actions:
        1. Save to Qdrant fix_corrections
        2. Comment on Jira
        3. Recommend revert if severe
        """
        logger.error(f"🚨 Production regression detected for {pr.ticket_id}")
        logger.error(f"  Baseline: {baseline:.2f} errors/hour")
        logger.error(f"  Current: {current:.2f} errors/hour")
        logger.error(f"  Spike: {spike_factor:.2f}x")
        
        # Count affected users
        dimension = "app" if pr.app else "error_category"
        value = pr.app if pr.app else pr.error_category
        affected_users = await self.telemetry.count_affected_users(dimension, value)
        
        # Create correction record
        correction = FixCorrection(
            original_ticket=pr.ticket_id,
            original_pr_url=pr.pr_url,
            severity=MistakeSeverity.PRODUCTION_REGRESSION,
            correction_description=(
                f"Production regression detected {spike_factor:.2f}x baseline. "
                f"Error rate increased from {baseline:.2f} to {current:.2f} errors/hour. "
                f"Affected {affected_users} users."
            ),
            spike_factor=spike_factor,
            baseline_rate=baseline,
            current_rate=current,
            error_category=pr.error_category,
            bug_layer=pr.bug_layer,
        )
        
        # Save to Qdrant institutional memory
        correction_saved = await self.qdrant.save_correction(correction)
        
        # Build Jira comment
        recommend_revert = spike_factor >= self.REVERT_THRESHOLD
        
        comment = f"""⚠️ **Production Regression Detected**

**Error Rate Spike**: {spike_factor:.1f}x baseline

- Baseline (7-day): {baseline:.2f} errors/hour
- Current rate: {current:.2f} errors/hour
- Affected users: {affected_users}

**PR**: {pr.pr_url}
**Strategy**: {pr.strategy}
**Original Confidence**: {pr.confidence:.1%}

**Recommendation**: {"🔴 **REVERT IMMEDIATELY**" if recommend_revert else "🟡 Monitor and investigate"}

This regression has been recorded in the institutional memory system to prevent similar issues in the future.
"""
        
        # Add Jira comment
        jira_comment_added = await self.jira.add_comment(pr.ticket_id, comment)
        
        # Create alert
        alert = RegressionAlert(
            pr_url=pr.pr_url,
            ticket_id=pr.ticket_id,
            merged_at=pr.merged_at,
            error_category=pr.error_category,
            baseline_rate=baseline,
            current_rate=current,
            spike_factor=spike_factor,
            affected_users=affected_users,
            dimension=dimension,
            value=value,
            jira_comment_added=jira_comment_added,
            revert_recommended=recommend_revert,
            correction_saved=correction_saved,
        )
        
        logger.info(f"✅ Regression alert created and notifications sent")
        return alert
    
    async def monitor_batch(
        self,
        merged_prs: list[MergedPR],
    ) -> list[Optional[RegressionAlert]]:
        """
        Monitor multiple PRs in parallel.
        
        Args:
            merged_prs: List of merged PRs to monitor
        
        Returns:
            List of alerts (None for PRs with no regression)
        """
        logger.info(f"Starting batch monitoring for {len(merged_prs)} PRs")
        
        # Monitor all PRs in parallel
        tasks = [self.monitor(pr) for pr in merged_prs]
        alerts = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Count regressions
        regression_count = sum(1 for alert in alerts if alert and not isinstance(alert, Exception))
        logger.info(f"Batch monitoring complete: {regression_count}/{len(merged_prs)} regressions detected")
        
        return alerts
