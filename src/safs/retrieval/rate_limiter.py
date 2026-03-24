"""
Priority Rate Limiter - Async-safe budget sharding for expensive API calls.

NEW in v6.0 — protects against GitHub API rate limit exhaustion (5,000/hr)
by implementing two-tier priority budget sharding.

Master Prompt Reference: Section 4.2.1 - PriorityRateLimiter
"""

import asyncio
import time
from dataclasses import dataclass
from enum import IntEnum


class Priority(IntEnum):
    """Request priority levels."""
    P0 = 0  # Critical (Stage 6 fix generation)
    P1 = 1  # Important (Stage 5 context building)
    P2 = 2  # Normal (Stage 4 repo locator exploratory)
    P3 = 3  # Low (Stage 10+ async learning/validation)


@dataclass
class CallRecord:
    """Timestamp record of API call."""
    timestamp: float
    priority: Priority


class PriorityRateLimiter:
    """
    Two-tier rate limiter with priority budget sharding.
    
    Budget Allocation (Master Prompt Rule #23):
    - P0/P1 tier: 5 calls/min reserved, can burst into P2/P3 shard
    - P2/P3 tier: 3 calls/min shared, cannot consume P0/P1 budget
    
    Thread Safety:
    - asyncio.Lock ensures safe concurrent access
    - Rolling 60-second window cleanup
    """

    def __init__(
        self,
        p0_p1_budget: int = 5,
        p2_p3_budget: int = 3,
        window_seconds: int = 60,
    ):
        """
        Initialize rate limiter.
        
        Args:
            p0_p1_budget: Calls/minute for P0/P1 tier (default: 5)
            p2_p3_budget: Calls/minute for P2/P3 tier (default: 3)
            window_seconds: Rolling window duration (default: 60s)
        """
        self.p0_p1_budget = p0_p1_budget
        self.p2_p3_budget = p2_p3_budget
        self.window_seconds = window_seconds
        
        self._p0_p1_calls: list[CallRecord] = []
        self._p2_p3_calls: list[CallRecord] = []
        self._lock = asyncio.Lock()

    def _cleanup_old_calls(self, calls: list[CallRecord]) -> list[CallRecord]:
        """Remove calls outside the rolling window."""
        cutoff = time.time() - self.window_seconds
        return [c for c in calls if c.timestamp >= cutoff]

    def _count_calls_in_window(
        self, calls: list[CallRecord], priority: Priority
    ) -> int:
        """Count calls in window for specific priority."""
        return sum(1 for c in calls if c.priority == priority)

    async def acquire(self, priority: Priority) -> bool:
        """
        Attempt to acquire rate limit permit.
        
        Rules:
        - P0/P1: Can consume from P0/P1 shard, if exhausted can burst into P2/P3 shard
        - P2/P3: Can only consume from P2/P3 shard, CANNOT touch P0/P1 budget
        
        Args:
            priority: Request priority level
        
        Returns:
            True if permit acquired, False if rate limited
        """
        async with self._lock:
            now = time.time()
            
            # Cleanup expired calls
            self._p0_p1_calls = self._cleanup_old_calls(self._p0_p1_calls)
            self._p2_p3_calls = self._cleanup_old_calls(self._p2_p3_calls)
            
            if priority in (Priority.P0, Priority.P1):
                # P0/P1 logic: Try primary shard first, then burst into secondary
                p0_p1_used = len(self._p0_p1_calls)
                
                if p0_p1_used < self.p0_p1_budget:
                    # Primary shard has capacity
                    self._p0_p1_calls.append(
                        CallRecord(timestamp=now, priority=priority)
                    )
                    return True
                
                # Primary exhausted — try bursting into P2/P3 shard
                p2_p3_used = len(self._p2_p3_calls)
                if p2_p3_used < self.p2_p3_budget:
                    self._p2_p3_calls.append(
                        CallRecord(timestamp=now, priority=priority)
                    )
                    return True
                
                # Both shards exhausted
                return False
            
            else:
                # P2/P3 logic: Only consume from secondary shard
                p2_p3_used = len(self._p2_p3_calls)
                
                if p2_p3_used < self.p2_p3_budget:
                    self._p2_p3_calls.append(
                        CallRecord(timestamp=now, priority=priority)
                    )
                    return True
                
                # Secondary shard exhausted, cannot burst into primary
                return False

    async def wait_for_capacity(self, priority: Priority, timeout: float = 120.0):
        """
        Wait until capacity is available or timeout.
        
        Args:
            priority: Request priority level
            timeout: Maximum wait time in seconds
        
        Raises:
            asyncio.TimeoutError: If capacity not available within timeout
        """
        start_time = time.time()
        
        while True:
            if await self.acquire(priority):
                return
            
            if time.time() - start_time >= timeout:
                raise asyncio.TimeoutError(
                    f"Rate limiter timeout after {timeout}s for {priority.name}"
                )
            
            # Exponential backoff with jitter
            await asyncio.sleep(min(2.0, 0.1 * (1.2 ** (time.time() - start_time))))

    def get_usage_stats(self) -> dict:
        """Get current usage statistics."""
        async def _get_stats():
            async with self._lock:
                self._p0_p1_calls = self._cleanup_old_calls(self._p0_p1_calls)
                self._p2_p3_calls = self._cleanup_old_calls(self._p2_p3_calls)
                
                return {
                    "p0_p1_used": len(self._p0_p1_calls),
                    "p0_p1_budget": self.p0_p1_budget,
                    "p0_p1_available": self.p0_p1_budget - len(self._p0_p1_calls),
                    "p2_p3_used": len(self._p2_p3_calls),
                    "p2_p3_budget": self.p2_p3_budget,
                    "p2_p3_available": self.p2_p3_budget - len(self._p2_p3_calls),
                    "window_seconds": self.window_seconds,
                }
        
        # Return coroutine for async callers
        return asyncio.create_task(_get_stats())
