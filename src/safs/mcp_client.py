"""
SAFS v6.0 — MCP Client Factory

Parses ``mcp_config/safs_mcp.json`` and creates typed wrapper clients for
each MCP server defined in the configuration.

Typed clients available
-----------------------
- :class:`VizioRemoteClient` — ``vizio-remote`` MCP server
- :class:`VizioSSHClient` — ``vizio-ssh`` MCP server
- :class:`VizioLokiClient` — ``vizio-loki`` MCP server

Connection lifecycle
--------------------
Each :class:`MCPClient` maintains a persistent connection that auto-reconnects
on failure.  Call ``await client.connect()`` before use and
``await client.disconnect()`` when done (or use as an async context manager).

Example usage::

    factory = MCPClientFactory()
    clients = await factory.create_from_config(Path("mcp_config/safs_mcp.json"))
    remote = clients.get("vizio-remote")
    if remote:
        result = await remote.call("send_key_press", key="KEY_OK")
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

# ─── Timeouts ─────────────────────────────────────────────────────────────────
_CONNECT_TIMEOUT = 10.0
_CALL_TIMEOUT = 30.0
_MAX_RETRIES = 3


class MCPClientError(Exception):
    """Raised on MCP call failures."""


class MCPClient:
    """
    Generic async MCP server wrapper.

    Communicates with an MCP server over HTTP (JSON-RPC style).
    Auto-reconnects on connection loss.

    Args:
        name: Logical name of this MCP server.
        base_url: HTTP base URL of the MCP server.
        timeout: Per-call timeout in seconds.
    """

    def __init__(
        self,
        name: str,
        base_url: str,
        timeout: float = _CALL_TIMEOUT,
    ) -> None:
        self.name = name
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._http: Optional[httpx.AsyncClient] = None

    async def connect(self) -> None:
        """Open the underlying HTTP connection pool."""
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(self._timeout, connect=_CONNECT_TIMEOUT),
        )
        logger.debug("MCPClient '%s' connected to %s", self.name, self._base_url)

    async def disconnect(self) -> None:
        """Close the connection pool."""
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def __aenter__(self) -> "MCPClient":
        await self.connect()
        return self

    async def __aexit__(self, *args) -> None:
        await self.disconnect()

    async def call(self, tool_name: str, **params: Any) -> Any:
        """
        Invoke an MCP tool by name.

        Retries up to :pydata:`_MAX_RETRIES` times on connection errors.

        Args:
            tool_name: Name of the MCP tool to invoke.
            **params: Keyword arguments forwarded to the tool.

        Returns:
            Tool result (parsed JSON).

        Raises:
            MCPClientError: On persistent failure after all retries.
        """
        if self._http is None:
            await self.connect()

        payload = {"tool": tool_name, "params": params}
        last_exc: Optional[Exception] = None

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                resp = await self._http.post(  # type: ignore[union-attr]
                    "/call", json=payload
                )
                resp.raise_for_status()
                return resp.json().get("result")
            except httpx.ConnectError as exc:
                last_exc = exc
                logger.warning(
                    "MCPClient '%s' connect error (attempt %d/%d): %s",
                    self.name,
                    attempt,
                    _MAX_RETRIES,
                    exc,
                )
                # Re-create client on connection error
                await self.disconnect()
                await self.connect()
            except httpx.HTTPStatusError as exc:
                raise MCPClientError(
                    f"MCP call '{tool_name}' failed with HTTP {exc.response.status_code}"
                ) from exc
            except Exception as exc:
                raise MCPClientError(
                    f"MCP call '{tool_name}' failed: {exc}"
                ) from exc

        raise MCPClientError(
            f"MCP call '{tool_name}' failed after {_MAX_RETRIES} retries: {last_exc}"
        )


class VizioRemoteClient(MCPClient):
    """
    Typed MCP client for the ``vizio-remote`` server.

    Provides helper methods for common remote-control operations.
    """

    def __init__(self, base_url: str) -> None:
        super().__init__("vizio-remote", base_url)

    async def send_key_press(self, key: str) -> Any:
        """Send a single IR key press."""
        return await self.call("send_key_press", key=key)

    async def launch_app(self, app_id: str) -> Any:
        """Launch a streaming app by app ID."""
        return await self.call("launch_app", app_id=app_id)

    async def get_current_app(self) -> str:
        """Return the ID of the currently active app."""
        result = await self.call("get_current_app")
        return str(result) if result else ""


class VizioSSHClient(MCPClient):
    """
    Typed MCP client for the ``vizio-ssh`` server.

    Provides helper methods for SSH-based TV file system operations.
    """

    def __init__(self, base_url: str) -> None:
        super().__init__("vizio-ssh", base_url)

    async def run_command(self, command: str) -> str:
        """Execute a shell command on the TV and return stdout."""
        result = await self.call("run_command", command=command)
        return str(result) if result else ""

    async def push_file(self, local_path: str, remote_path: str) -> None:
        """Copy a local file to the TV via SCP."""
        await self.call("push_file", local_path=local_path, remote_path=remote_path)

    async def read_file(self, remote_path: str) -> str:
        """Read a file from the TV and return its contents."""
        result = await self.call("read_file", remote_path=remote_path)
        return str(result) if result else ""


class VizioLokiClient(MCPClient):
    """
    Typed MCP client for the ``vizio-loki`` server.

    Provides helper methods for LOKi log streaming and analysis.
    """

    def __init__(self, base_url: str) -> None:
        super().__init__("vizio-loki", base_url)

    async def get_recent_logs(self, lines: int = 200) -> str:
        """Return the most recent *lines* lines from the LOKi log stream."""
        result = await self.call("get_recent_logs", lines=lines)
        return str(result) if result else ""

    async def get_crash_tombstones(self) -> list[str]:
        """Return paths to any crash tombstones found on the TV."""
        result = await self.call("get_crash_tombstones")
        if isinstance(result, list):
            return result
        return []


class MCPClientFactory:
    """
    Creates typed :class:`MCPClient` wrappers from a ``safs_mcp.json`` config.

    Example config structure::

        {
          "servers": {
            "vizio-remote": { "url": "http://127.0.0.1:3001" },
            "vizio-ssh":    { "url": "http://127.0.0.1:3002" },
            "vizio-loki":   { "url": "http://127.0.0.1:3003" }
          }
        }
    """

    _TYPED_CLIENTS: dict[str, type] = {
        "vizio-remote": VizioRemoteClient,
        "vizio-ssh": VizioSSHClient,
        "vizio-loki": VizioLokiClient,
    }

    async def create_from_config(
        self, config_path: Path
    ) -> dict[str, MCPClient]:
        """
        Parse *config_path* and return a dict of connected MCP clients.

        Args:
            config_path: Path to ``safs_mcp.json``.

        Returns:
            Dict mapping server name → :class:`MCPClient` instance.

        Raises:
            FileNotFoundError: If *config_path* does not exist.
            ValueError: If the JSON cannot be parsed.
        """
        if not config_path.exists():
            raise FileNotFoundError(f"MCP config not found: {config_path}")

        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid MCP config JSON: {exc}") from exc

        servers: dict = config.get("servers", config.get("mcpServers", {}))
        clients: dict[str, MCPClient] = {}

        for name, server_config in servers.items():
            url = server_config.get("url", "")
            if not url:
                logger.warning("No URL configured for MCP server '%s'; skipping", name)
                continue

            cls = self._TYPED_CLIENTS.get(name, MCPClient)
            if cls is MCPClient:
                client = MCPClient(name=name, base_url=url)
            else:
                client = cls(base_url=url)

            clients[name] = client
            logger.debug("Created MCP client for '%s' at %s", name, url)

        return clients
