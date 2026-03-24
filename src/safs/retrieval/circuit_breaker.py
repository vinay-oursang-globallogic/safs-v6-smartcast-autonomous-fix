"""
SAFS v6.0 — Circuit Breaker

Implements the classic three-state circuit breaker pattern to protect SAFS
from cascading failures when external services (GitHub MCP, Qdrant, Jira) are
unavailable.

States
------
- **CLOSED** — normal operation; calls pass through.
- **OPEN** — service is considered unavailable; calls are refused immediately.
- **HALF_OPEN** — probe state; a limited number of calls are allowed.

Transitions
-----------
- CLOSED → OPEN: ``failure_threshold`` consecutive failures.
- OPEN → HALF_OPEN: after ``recovery_timeout`` seconds.
- HALF_OPEN → CLOSED: ``success_threshold`` consecutive successes.
- HALF_OPEN → OPEN: first failure.

Example usage::

    cb = CircuitBreaker(name="github_mcp", failure_threshold=5)
    try:
        result = await cb.call(my_github_client.search_code, "query")
    except CircuitOpenError:
        result = await fallback_client.search_code("query")
"""

from __future__ import annotations

import asyncio
import logging
import time
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    """Three states of the circuit breaker."""

    CLOSED = "closed"       # Normal – requests pass through
    OPEN = "open"           # Tripped – requests rejected immediately
    HALF_OPEN = "half_open" # Probe – limited requests allowed


class CircuitOpenError(Exception):
    """
    Raised when a call is attempted while the circuit is OPEN.

    Attributes:
        name: Circuit breaker name.
        seconds_until_retry: Approximate seconds until the next HALF_OPEN probe.
    """

    def __init__(self, name: str, seconds_until_retry: float) -> None:
        self.name = name
        self.seconds_until_retry = round(seconds_until_retry, 1)
        super().__init__(
            f"Circuit '{name}' is OPEN. Retry in {self.seconds_until_retry}s."
        )


class CircuitBreaker:
    """
    Async circuit breaker protecting a single external dependency.

    Args:
        name: Human-readable identifier (used in logs and exceptions).
        failure_threshold: Consecutive failures before tripping OPEN.
        recovery_timeout: Seconds in OPEN state before transitioning to
            HALF_OPEN.
        success_threshold: Consecutive successes in HALF_OPEN to close.
    """

    def __init__(
        self,
        name: str = "unnamed",
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        success_threshold: int = 2,
    ) -> None:
        self.name = name
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._success_threshold = success_threshold

        self._state: CircuitState = CircuitState.CLOSED
        self._failure_count: int = 0
        self._success_count: int = 0
        self._last_failure_time: float = 0.0
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        """Current circuit state (may transition OPEN→HALF_OPEN on read)."""
        self._check_recovery()
        return self._state

    async def call(
        self, func: Callable[..., Any], /, *args: Any, **kwargs: Any
    ) -> Any:
        """
        Execute *func* with circuit breaker protection.

        Args:
            func: Async callable to wrap.
            *args: Positional arguments forwarded to *func*.
            **kwargs: Keyword arguments forwarded to *func*.

        Returns:
            Whatever *func* returns.

        Raises:
            CircuitOpenError: If the circuit is currently OPEN.
            Exception: Any exception raised by *func* (after recording failure).
        """
        async with self._lock:
            self._check_recovery()
            state = self._state

        if state == CircuitState.OPEN:
            seconds_remaining = self._recovery_timeout - (
                time.monotonic() - self._last_failure_time
            )
            raise CircuitOpenError(self.name, max(0.0, seconds_remaining))

        try:
            result = await func(*args, **kwargs)
        except Exception as exc:
            async with self._lock:
                self._record_failure()
            logger.warning(
                "CircuitBreaker '%s' recorded failure: %s", self.name, exc
            )
            raise

        async with self._lock:
            self._record_success()

        return result

    def reset(self) -> None:
        """Manually reset the circuit breaker to CLOSED state."""
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        logger.info("CircuitBreaker '%s' manually reset to CLOSED", self.name)

    # ── Private ───────────────────────────────────────────────────────────────

    def _check_recovery(self) -> None:
        """Transition OPEN → HALF_OPEN if recovery_timeout has elapsed."""
        if self._state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= self._recovery_timeout:
                self._state = CircuitState.HALF_OPEN
                self._success_count = 0
                logger.info(
                    "CircuitBreaker '%s' transitioned OPEN → HALF_OPEN "
                    "after %.1fs",
                    self.name,
                    elapsed,
                )

    def _record_failure(self) -> None:
        self._failure_count += 1
        self._success_count = 0
        self._last_failure_time = time.monotonic()

        if self._state == CircuitState.HALF_OPEN:
            self._state = CircuitState.OPEN
            logger.warning(
                "CircuitBreaker '%s' HALF_OPEN → OPEN (probe failed)",
                self.name,
            )
        elif (
            self._state == CircuitState.CLOSED
            and self._failure_count >= self._failure_threshold
        ):
            self._state = CircuitState.OPEN
            logger.error(
                "CircuitBreaker '%s' tripped CLOSED → OPEN after %d failures",
                self.name,
                self._failure_count,
            )

    def _record_success(self) -> None:
        self._failure_count = 0
        if self._state == CircuitState.HALF_OPEN:
            self._success_count += 1
            if self._success_count >= self._success_threshold:
                self._state = CircuitState.CLOSED
                logger.info(
                    "CircuitBreaker '%s' HALF_OPEN → CLOSED after %d successes",
                    self.name,
                    self._success_count,
                )
