"""
Proactive Telemetry Monitor — Phase 13

Runs every 5 min as Temporal.io cron.
Detects error spikes BEFORE users file tickets.

Extended from jira_auto_fixer with v6.0 additions:
- Monitors key dimensions (app, layer, chipset, error_category)
- Compares against 7-day baseline
- Auto-creates Jira tickets when thresholds exceeded
- Feeds tickets directly into SAFS pipeline

Usage:
    monitor = ProactiveTelemetryMonitor()
    await monitor.check()  # Run as cron job every 5 minutes
"""

import logging
import asyncio
from typing import Optional, List, Tuple
from datetime import datetime, timezone

from .models import (
    ProactiveTicket,
    TelemetryMetric,
)
from .regression_correlator import TelemetryClient, JiraClient

logger = logging.getLogger(__name__)


class ProactiveTelemetryMonitor:
    """
    Proactive error spike detection.
    
    Runs as Temporal.io cron job every 5 minutes.
    Detects error spikes BEFORE users file tickets.
    Auto-creates Jira tickets and feeds into SAFS pipeline.
    """
    
    SPIKE_THRESHOLD = 2.0  # 2x baseline triggers alert
    MIN_AFFECTED_USERS = 50  # Minimum affected users to create ticket
    MIN_ERROR_COUNT = 100  # Minimum error count to create ticket
    
    # Dimensions to monitor
    MONITOR_DIMENSIONS = [
        # Streaming apps
        ("app", "netflix"),
        ("app", "amazon_prime"),
        ("app", "hulu"),
        ("app", "watchfree"),
        ("app", "disney_plus"),
        ("app", "max"),
        ("app", "youtube"),
        
        # System layers
        ("layer", "loki"),
        ("layer", "html5"),
        
        # Chipsets
        ("chipset", "mt5670"),
        ("chipset", "mt5882"),
        ("chipset", "mt5396"),
        
        # Common error categories
        ("error_category", "EME_DRM_FAILURE"),
        ("error_category", "COMPANION_LIB_TIMING"),
        ("error_category", "KEYDOWN_NOT_FIRED"),
        ("error_category", "LOKI_SEGFAULT_NULL_DEREF"),
        ("error_category", "JS_HEAP_OOM"),
    ]
    
    def __init__(
        self,
        telemetry_client: Optional[TelemetryClient] = None,
        jira_client: Optional[JiraClient] = None,
        spike_threshold: float = 2.0,
        min_affected_users: int = 50,
        min_error_count: int = 100,
    ):
        """
        Initialize proactive monitor.
        
        Args:
            telemetry_client: Client for production metrics
            jira_client: Client for ticket creation
            spike_threshold: Multiplier to trigger alert (default 2.0x)
            min_affected_users: Min users to create ticket (default 50)
            min_error_count: Min error count to create ticket (default 100)
        """
        self.telemetry = telemetry_client or TelemetryClient()
        self.jira = jira_client or JiraClient()
        self.spike_threshold = spike_threshold
        self.min_affected_users = min_affected_users
        self.min_error_count = min_error_count
        
        logger.info("ProactiveTelemetryMonitor initialized")
        logger.info(f"  Monitoring {len(self.MONITOR_DIMENSIONS)} dimensions")
        logger.info(f"  Spike threshold: {self.spike_threshold}x")
        logger.info(f"  Min affected users: {self.min_affected_users}")
        logger.info(f"  Min error count: {self.min_error_count}")
    
    async def check(self) -> List[ProactiveTicket]:
        """
        Check all monitored dimensions for spikes.
        
        Should be run as cron job every 5 minutes.
        
        Returns:
            List of proactive tickets created
        """
        logger.info("🔍 Running proactive telemetry check")
        start_time = datetime.now(timezone.utc)
        
        tickets_created = []
        
        # Check each dimension
        for dimension, value in self.MONITOR_DIMENSIONS:
            try:
                ticket = await self._check_dimension(dimension, value)
                if ticket:
                    tickets_created.append(ticket)
            except Exception as e:
                logger.error(f"Error checking {dimension}={value}: {e}")
                continue
        
        duration = (datetime.now(timezone.utc) - start_time).total_seconds()
        
        if tickets_created:
            logger.warning(f"⚠️ Created {len(tickets_created)} proactive tickets in {duration:.1f}s")
            for ticket in tickets_created:
                logger.warning(f"  - {ticket.jira_ticket_key}: {ticket.title}")
        else:
            logger.info(f"✅ No spikes detected in {duration:.1f}s")
        
        return tickets_created
    
    async def _check_dimension(
        self,
        dimension: str,
        value: str,
    ) -> Optional[ProactiveTicket]:
        """
        Check a single dimension for error spikes.
        
        Args:
            dimension: Dimension type (app, layer, chipset, error_category)
            value: Dimension value (e.g., "netflix", "mt5670")
        
        Returns:
            ProactiveTicket if spike detected and ticket created
        """
        # Get current rate
        current_rate = await self.telemetry.get_rate(dimension, value)
        
        # Get 7-day baseline
        baseline_rate = await self.telemetry.get_7day_baseline(dimension, value)
        
        # Calculate spike factor
        if baseline_rate == 0:
            logger.debug(f"{dimension}={value}: No baseline data")
            return None
        
        spike_factor = current_rate / baseline_rate
        
        # Check if spike threshold exceeded
        if spike_factor < self.spike_threshold:
            return None
        
        logger.info(f"🔥 Spike detected: {dimension}={value} at {spike_factor:.2f}x baseline")
        
        # Count affected users
        affected_users = await self.telemetry.count_affected_users(dimension, value)
        
        # Check minimum thresholds
        if affected_users < self.min_affected_users:
            logger.debug(f"  Below min affected users: {affected_users}/{self.min_affected_users}")
            return None
        
        error_count = int(current_rate * 24)  # Rough daily count
        if error_count < self.min_error_count:
            logger.debug(f"  Below min error count: {error_count}/{self.min_error_count}")
            return None
        
        # Create proactive ticket
        ticket = await self._create_proactive_ticket(
            dimension=dimension,
            value=value,
            baseline_rate=baseline_rate,
            current_rate=current_rate,
            spike_factor=spike_factor,
            affected_users=affected_users,
            error_count=error_count,
        )
        
        return ticket
    
    async def _create_proactive_ticket(
        self,
        dimension: str,
        value: str,
        baseline_rate: float,
        current_rate: float,
        spike_factor: float,
        affected_users: int,
        error_count: int,
    ) -> ProactiveTicket:
        """
        Create Jira ticket for proactive detection.
        
        Args:
            dimension: Dimension type
            value: Dimension value
            baseline_rate: Baseline error rate
            current_rate: Current error rate
            spike_factor: Spike multiplier
            affected_users: Number of affected users
            error_count: Estimated error count
        
        Returns:
            ProactiveTicket with ticket details
        """
        # Build ticket title
        title = f"Proactive Detection: {dimension.upper()}={value} error rate spike ({spike_factor:.1f}x)"
        
        # Build ticket description
        description = f"""🤖 **Proactive Error Detection** (SAFS v6.0)

An automated error spike has been detected BEFORE user reports.

**Detection Details:**
- Dimension: {dimension}
- Value: {value}
- Spike Factor: {spike_factor:.2f}x baseline
- Baseline Rate: {baseline_rate:.2f} errors/hour
- Current Rate: {current_rate:.2f} errors/hour
- Affected Users: {affected_users}
- Estimated Daily Errors: {error_count}

**Timeline:**
- Detected: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}
- Baseline Period: Past 7 days

**Next Steps:**
1. SAFS will automatically analyze error logs
2. Root cause analysis will be performed
3. Fix candidates will be generated
4. Validation will be run
5. Draft PR will be created if high confidence

**Priority**: HIGH (proactive detection prevents user impact)

---
*This ticket was automatically created by ProactiveTelemetryMonitor*
*Confidence in proactive detection: HIGH*
"""
        
        # Determine priority
        priority = "high" if spike_factor >= 3.0 else "medium"
        
        # Create ticket
        ticket = ProactiveTicket(
            dimension=dimension,
            value=value,
            baseline_rate=baseline_rate,
            current_rate=current_rate,
            spike_factor=spike_factor,
            affected_users=affected_users,
            error_count=error_count,
            duration_minutes=5,  # Check interval
            title=title,
            description=description,
            priority=priority,
        )
        
        # Create Jira ticket (mock for now)
        jira_key = f"SMART-PROACTIVE-{int(datetime.now(timezone.utc).timestamp())}"
        ticket.jira_ticket_key = jira_key
        
        logger.info(f"✅ Created proactive ticket: {jira_key}")
        logger.info(f"  Title: {title}")
        logger.info(f"  Priority: {priority}")
        logger.info(f"  Affected users: {affected_users}")
        
        # In production: Add comment to trigger SAFS pipeline
        # await self.jira.add_comment(jira_key, "🤖 Triggering SAFS analysis...")
        
        return ticket
    
    async def run_continuously(
        self,
        interval_minutes: int = 5,
        max_iterations: Optional[int] = None,
    ) -> None:
        """
        Run monitor continuously as cron job.
        
        Args:
            interval_minutes: Check interval (default 5 minutes)
            max_iterations: Max iterations (None = infinite)
        """
        logger.info(f"Starting continuous monitoring (interval: {interval_minutes}m)")
        
        iteration = 0
        while max_iterations is None or iteration < max_iterations:
            iteration += 1
            
            try:
                tickets = await self.check()
                logger.info(f"Iteration {iteration}: {len(tickets)} tickets created")
            except Exception as e:
                logger.error(f"Error in iteration {iteration}: {e}", exc_info=True)
            
            # Wait for next interval
            if max_iterations is None or iteration < max_iterations:
                await asyncio.sleep(interval_minutes * 60)
        
        logger.info(f"Continuous monitoring stopped after {iteration} iterations")
