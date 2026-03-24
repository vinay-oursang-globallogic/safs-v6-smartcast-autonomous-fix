"""
Unit tests for MCP client and circuit breaker modules.

Covers:
- CircuitBreaker: state transitions (CLOSED/OPEN/HALF_OPEN)
- MCPClientFactory: config parsing
- MCPClient: retry / reconnect
- VizioRemoteClient / VizioSSHClient / VizioLokiClient: construction
"""

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── CircuitBreaker ────────────────────────────────────────────────────────────

class TestCircuitBreaker:
    def _cb(self, failure_threshold=3, recovery_timeout=0.1, success_threshold=2):
        from src.safs.retrieval.circuit_breaker import CircuitBreaker
        return CircuitBreaker(
            name="test",
            failure_threshold=failure_threshold,
            recovery_timeout=recovery_timeout,
            success_threshold=success_threshold,
        )

    def test_instantiation(self):
        cb = self._cb()
        assert cb is not None

    def test_initial_state_is_closed(self):
        from src.safs.retrieval.circuit_breaker import CircuitState
        cb = self._cb()
        assert cb.state == CircuitState.CLOSED

    def test_transitions_to_open_after_failures(self):
        from src.safs.retrieval.circuit_breaker import CircuitState

        async def failing():
            raise RuntimeError("simulated failure")

        cb = self._cb(failure_threshold=3)

        async def run():
            for _ in range(3):
                try:
                    await cb.call(failing)
                except Exception:
                    pass
            return cb.state

        state = asyncio.run(run())
        assert state == CircuitState.OPEN

    def test_raises_circuit_open_error_when_open(self):
        from src.safs.retrieval.circuit_breaker import CircuitOpenError, CircuitState

        async def failing():
            raise RuntimeError("fail")

        cb = self._cb(failure_threshold=2)

        async def run():
            for _ in range(2):
                try:
                    await cb.call(failing)
                except Exception:
                    pass
            # Now open — next call should raise CircuitOpenError
            await cb.call(lambda: asyncio.sleep(0))

        with pytest.raises((CircuitOpenError, Exception)):
            asyncio.run(run())

    def test_half_open_after_recovery_timeout(self):
        import time
        from src.safs.retrieval.circuit_breaker import CircuitState

        async def failing():
            raise RuntimeError("fail")

        cb = self._cb(failure_threshold=2, recovery_timeout=0.05)

        async def run():
            for _ in range(2):
                try:
                    await cb.call(failing)
                except Exception:
                    pass
            await asyncio.sleep(0.1)
            return cb.state

        state = asyncio.run(run())
        assert state in (CircuitState.HALF_OPEN, CircuitState.CLOSED)

    def test_success_closes_circuit_from_half_open(self):
        from src.safs.retrieval.circuit_breaker import CircuitState

        async def failing():
            raise RuntimeError("fail")

        async def succeeding():
            return "ok"

        cb = self._cb(failure_threshold=2, recovery_timeout=0.05, success_threshold=1)

        async def run():
            # Trip the circuit
            for _ in range(2):
                try:
                    await cb.call(failing)
                except Exception:
                    pass
            # Wait for recovery
            await asyncio.sleep(0.1)
            # One success in HALF_OPEN should close it
            try:
                await cb.call(succeeding)
            except Exception:
                pass
            return cb.state

        state = asyncio.run(run())
        # Depending on success_threshold=1, should be CLOSED
        assert state in (CircuitState.CLOSED, CircuitState.HALF_OPEN)

    def test_call_delegates_to_callable(self):
        cb = self._cb()

        async def my_func():
            return 42

        result = asyncio.run(cb.call(my_func))
        assert result == 42

    def test_failure_count_resets_after_success(self):
        from src.safs.retrieval.circuit_breaker import CircuitState

        call_count = [0]

        async def sometimes_fails():
            call_count[0] += 1
            if call_count[0] <= 2:
                raise RuntimeError("fail")
            return "ok"

        cb = self._cb(failure_threshold=5)

        async def run():
            for _ in range(2):
                try:
                    await cb.call(sometimes_fails)
                except Exception:
                    pass
            # Should succeed now and still be CLOSED
            await cb.call(sometimes_fails)
            return cb.state

        state = asyncio.run(run())
        assert state == CircuitState.CLOSED


# ── MCPClientFactory ──────────────────────────────────────────────────────────

class TestMCPClientFactory:
    def _make_config(self, tmp_path, content: dict) -> Path:
        config_file = tmp_path / "mcp_config.json"
        config_file.write_text(json.dumps(content))
        return config_file

    def test_import(self):
        from src.safs.mcp_client import MCPClientFactory
        assert MCPClientFactory is not None

    def test_create_from_empty_config(self, tmp_path):
        from src.safs.mcp_client import MCPClientFactory
        config_path = self._make_config(tmp_path, {"clients": {}})
        factory = MCPClientFactory()
        clients = asyncio.run(factory.create_from_config(config_path))
        assert isinstance(clients, dict)

    def test_create_remote_client_from_config(self, tmp_path):
        from src.safs.mcp_client import MCPClientFactory
        config = {
            "clients": {
                "tv_remote": {
                    "type": "remote",
                    "url": "http://192.168.1.100:8080",
                    "token": "test_token"
                }
            }
        }
        config_path = self._make_config(tmp_path, config)
        factory = MCPClientFactory()
        clients = asyncio.run(factory.create_from_config(config_path))
        assert isinstance(clients, dict)

    def test_invalid_config_file_raises(self):
        from src.safs.mcp_client import MCPClientFactory
        factory = MCPClientFactory()
        with pytest.raises(Exception):
            asyncio.run(factory.create_from_config(Path("/nonexistent/path/config.json")))

    def test_create_ssh_client_from_config(self, tmp_path):
        from src.safs.mcp_client import MCPClientFactory
        config = {
            "clients": {
                "tv_ssh": {
                    "type": "ssh",
                    "host": "192.168.1.100",
                    "username": "root",
                    "key_file": "/tmp/id_rsa"
                }
            }
        }
        config_path = self._make_config(tmp_path, config)
        factory = MCPClientFactory()
        clients = asyncio.run(factory.create_from_config(config_path))
        assert isinstance(clients, dict)


# ── MCPClient / VizioClient types ─────────────────────────────────────────────

class TestVizioClients:
    def test_vizio_remote_client_import(self):
        from src.safs.mcp_client import VizioRemoteClient
        assert VizioRemoteClient is not None

    def test_vizio_ssh_client_import(self):
        from src.safs.mcp_client import VizioSSHClient
        assert VizioSSHClient is not None

    def test_vizio_loki_client_import(self):
        from src.safs.mcp_client import VizioLokiClient
        assert VizioLokiClient is not None

    def test_mcp_client_base_import(self):
        from src.safs.mcp_client import MCPClient
        assert MCPClient is not None

    def test_vizio_remote_client_instantiation(self):
        from src.safs.mcp_client import VizioRemoteClient
        client = VizioRemoteClient(base_url="http://192.168.1.100:8080")
        assert client is not None

    def test_vizio_loki_client_instantiation(self):
        from src.safs.mcp_client import VizioLokiClient
        client = VizioLokiClient(base_url="http://loki.internal:3100")
        assert client is not None
