"""
SAFS v6.0 — Telemetry Client

Provides a unified interface for querying TV fleet metrics from the configured
telemetry backend (Prometheus or noop/testing).

Usage
-----
Inject via dependency injection into ``proactive_monitor.py`` and
``regression_correlator.py`` instead of the hardcoded mock values.

Example::

    from safs.telemetry.telemetry_client import PrometheusTelemetryClient
    client = PrometheusTelemetryClient(prometheus_url="http://prometheus:9090")
    rate = await client.get_rate("error_category", "LOKI_SEGFAULT_NULL_DEREF", 24)
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# ─── Timeouts ─────────────────────────────────────────────────────────────────
_HTTP_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class TelemetryClient(ABC):
    """
    Abstract telemetry client.

    All implementations must be async-safe.
    """

    @abstractmethod
    async def get_rate(
        self, dimension: str, value: str, window_hours: float
    ) -> float:
        """
        Return the current error rate for a given dimension/value combination.

        Args:
            dimension: Metric dimension (e.g., ``"error_category"``).
            value: Dimension value (e.g., ``"LOKI_SEGFAULT_NULL_DEREF"``).
            window_hours: Look-back window in hours.

        Returns:
            Error rate as a float (events/hour or similar normalized unit).
        """

    @abstractmethod
    async def get_baseline(
        self, dimension: str, value: str, window_hours: float
    ) -> float:
        """
        Return the baseline error rate for the pre-deployment window.

        Args:
            dimension: Metric dimension.
            value: Dimension value.
            window_hours: Baseline window in hours.

        Returns:
            Baseline rate as float.
        """

    @abstractmethod
    async def count_affected_users(
        self, dimension: str, value: str
    ) -> int:
        """
        Return the number of unique users affected by an error category.

        Args:
            dimension: Metric dimension.
            value: Dimension value.

        Returns:
            Count of unique affected users.
        """


class PrometheusTelemetryClient(TelemetryClient):
    """
    Telemetry client backed by the Prometheus HTTP API.

    Queries are executed via the ``/api/v1/query`` instant query endpoint.
    Falls back to mock/zero values when Prometheus is unreachable.

    Args:
        prometheus_url: Base URL of the Prometheus server
            (e.g., ``"http://prometheus:9090"``).
        timeout: HTTP request timeout.
    """

    def __init__(
        self,
        prometheus_url: str,
        timeout: httpx.Timeout = _HTTP_TIMEOUT,
    ) -> None:
        self._url = prometheus_url.rstrip("/")
        self._http = httpx.AsyncClient(
            base_url=self._url,
            timeout=timeout,
        )

    async def get_rate(
        self, dimension: str, value: str, window_hours: float
    ) -> float:
        """Query rate from Prometheus using ``rate()`` function."""
        window = f"{int(window_hours)}h"
        promql = (
            f'sum(rate(safs_errors_total{{{dimension}="{value}"}}[{window}]))'
        )
        result = await self._instant_query(promql)
        return result if result is not None else 0.0

    async def get_baseline(
        self, dimension: str, value: str, window_hours: float
    ) -> float:
        """Query baseline (average rate) from Prometheus."""
        window = f"{int(window_hours)}h"
        promql = (
            f'avg_over_time(rate(safs_errors_total{{{dimension}="{value}"}}[1h])[{window}:1h])'
        )
        result = await self._instant_query(promql)
        return result if result is not None else 0.0

    async def count_affected_users(
        self, dimension: str, value: str
    ) -> int:
        """Query unique user count from Prometheus."""
        promql = (
            f'count(count by (user_id)(safs_errors_total{{{dimension}="{value}"}}))'
        )
        result = await self._instant_query(promql)
        return int(result) if result is not None else 0

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._http.aclose()

    # ── Private ───────────────────────────────────────────────────────────────

    async def _instant_query(self, promql: str) -> Optional[float]:
        """
        Execute a Prometheus instant query and extract the scalar result.

        Args:
            promql: PromQL expression.

        Returns:
            Float value or ``None`` on failure.
        """
        try:
            resp = await self._http.get(
                "/api/v1/query",
                params={"query": promql},
            )
            resp.raise_for_status()
            data = resp.json()

            results = data.get("data", {}).get("result", [])
            if not results:
                return None

            # Extract first result value
            value_tuple = results[0].get("value", [])
            if len(value_tuple) < 2:
                return None

            return float(value_tuple[1])

        except httpx.ConnectError:
            logger.debug(
                "Prometheus unreachable; returning None for query: %s",
                promql[:80],
            )
            return None
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            logger.warning(
                "Prometheus query failed: %s | query=%s", exc, promql[:80]
            )
            return None


class NoopTelemetryClient(TelemetryClient):
    """
    Noop telemetry client for testing and environments without monitoring.

    Always returns zero / sensible defaults.
    """

    async def get_rate(
        self, dimension: str, value: str, window_hours: float
    ) -> float:
        """Return 0.0 (no telemetry configured)."""
        return 0.0

    async def get_baseline(
        self, dimension: str, value: str, window_hours: float
    ) -> float:
        """Return 0.0 (no telemetry configured)."""
        return 0.0

    async def count_affected_users(
        self, dimension: str, value: str
    ) -> int:
        """Return 0 (no telemetry configured)."""
        return 0
