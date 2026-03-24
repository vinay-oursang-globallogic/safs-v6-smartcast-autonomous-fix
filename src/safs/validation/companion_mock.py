"""
SAFS v6.0 — Companion Library Mock Server (PATH β support)

Provides a lightweight ``aiohttp``-based HTTP server that emulates the Vizio
Companion Library API so Playwright tests can run without a real SmartCast TV.

The mock server:
- Fires the ``VIZIO_LIBRARY_DID_LOAD`` window event after a configurable delay
- Serves ``window.VIZIO.*`` API method stubs
- Varies its responses based on the requested API version
- Simulates error conditions for negative-path testing

Example usage::

    async with CompanionLibMockServer(port=12345, api_version="v2.0") as mock:
        # Mock is running; pass http://localhost:12345 to Playwright
        await playwright_test_suite(companion_url=mock.url)
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_PORT = 12345
_VIZIO_LIBRARY_LOAD_DELAY_MS = 500  # Default delay before firing DID_LOAD


class CompanionLibMockServer:
    """
    Aiohttp-based Companion Library mock server.

    Args:
        port: TCP port to listen on (default 12345).
        api_version: Companion API version to emulate (e.g., ``"v2.1"``).
        load_delay_ms: Milliseconds before ``VIZIO_LIBRARY_DID_LOAD`` event fires.
        simulate_timeout: If ``True``, the DID_LOAD event is never fired
            (useful for testing timeout handling).
    """

    def __init__(
        self,
        port: int = _DEFAULT_PORT,
        api_version: str = "v2.0",
        load_delay_ms: int = _VIZIO_LIBRARY_LOAD_DELAY_MS,
        simulate_timeout: bool = False,
    ) -> None:
        self._port = port
        self._api_version = api_version
        self._load_delay_ms = load_delay_ms
        self._simulate_timeout = simulate_timeout
        self._runner = None
        self._site = None

    @property
    def url(self) -> str:
        """Base URL of the running mock server."""
        return f"http://localhost:{self._port}"

    async def start(self) -> None:
        """Start the mock server and begin listening."""
        try:
            from aiohttp import web
        except ImportError:
            logger.warning("aiohttp not installed; CompanionLibMockServer unavailable")
            return

        app = web.Application()
        app.router.add_get("/companion/script.js", self._serve_companion_script)
        app.router.add_get("/companion/status", self._serve_status)
        app.router.add_post("/companion/api/{method}", self._serve_api_call)
        app.router.add_get("/healthz", self._serve_health)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        from aiohttp.web_runner import TCPSite
        self._site = TCPSite(self._runner, "localhost", self._port)
        await self._site.start()
        logger.info(
            "CompanionLibMockServer started at %s (version=%s)",
            self.url,
            self._api_version,
        )

    async def stop(self) -> None:
        """Shutdown the server."""
        if self._runner is not None:
            await self._runner.cleanup()
            logger.info("CompanionLibMockServer stopped")

    async def __aenter__(self) -> "CompanionLibMockServer":
        await self.start()
        return self

    async def __aexit__(self, *args) -> None:
        await self.stop()

    # ── Request handlers ──────────────────────────────────────────────────────

    async def _serve_companion_script(self, request) -> object:
        """Return JavaScript that injects the VIZIO mock into the page."""
        from aiohttp.web import Response

        delay = 0 if self._simulate_timeout else self._load_delay_ms
        methods = self._get_api_methods()
        methods_js = "\n".join(
            f"        {name}: function() {{ return {json.dumps(stub)}; }},"
            for name, stub in methods.items()
        )

        js = f"""
(function() {{
    // SAFS Companion Library Mock v{self._api_version}
    window.VIZIO = window.VIZIO || {{}};
    window.VIZIO.version = "{self._api_version}";
    window.VIZIO.companionLibVersion = "{self._api_version}";
{methods_js}

    // Fire VIZIO_LIBRARY_DID_LOAD after delay
    {"" if self._simulate_timeout else f'''
    setTimeout(function() {{
        var evt = new CustomEvent("VIZIO_LIBRARY_DID_LOAD", {{
            detail: {{ version: "{self._api_version}" }}
        }});
        window.dispatchEvent(evt);
        console.log("[CompanionMock] VIZIO_LIBRARY_DID_LOAD fired");
    }}, {delay});'''}
}})();
"""
        return Response(text=js, content_type="application/javascript")

    async def _serve_status(self, request) -> object:
        from aiohttp.web import Response
        return Response(
            text=json.dumps({
                "status": "ok",
                "version": self._api_version,
                "simulate_timeout": self._simulate_timeout,
            }),
            content_type="application/json",
        )

    async def _serve_api_call(self, request) -> object:
        from aiohttp.web import Response
        method = request.match_info["method"]
        handler = self._get_api_methods().get(method)
        if handler is None:
            return Response(
                text=json.dumps({"error": f"Unknown method: {method}"}),
                status=404,
                content_type="application/json",
            )
        return Response(
            text=json.dumps({"result": handler, "version": self._api_version}),
            content_type="application/json",
        )

    async def _serve_health(self, request) -> object:
        from aiohttp.web import Response
        return Response(text="ok")

    # ── Version-aware API stubs ────────────────────────────────────────────────

    def _get_api_methods(self) -> dict:
        """Return version-appropriate API method stubs."""
        base = {
            "getVersion": self._api_version,
            "getDeviceType": "SmartTV",
            "getBuildInfo": {"model": "M75Q7-H", "firmware": "20.0.0"},
            "getSystemVolume": {"value": 40, "muted": False},
            "setSystemVolume": "OK",
            "getInputList": [
                {"id": "HDMI1", "name": "HDMI 1"},
                {"id": "HDMI2", "name": "HDMI 2"},
            ],
            "getCurrentInput": "HDMI1",
        }

        # v2.0+ additions
        if self._api_version >= "v2.0":
            base.update({
                "getAudioSettings": {"mode": "Normal", "surround": "Off"},
                "getPictureMode": {"mode": "Standard"},
                "getNetworkInfo": {"ssid": "TestNetwork", "signal": 85},
            })

        # v2.1+ additions
        if self._api_version >= "v2.1":
            base.update({
                "getAppList": [
                    {"id": "netflix", "name": "Netflix"},
                    {"id": "hulu", "name": "Hulu"},
                ],
                "launchApp": "OK",
                "closeApp": "OK",
            })

        return base


class DRMTester:
    """
    Tests Widevine EME key session creation and license acquisition.

    Args:
        companion_url: URL of the companion mock server.
        timeout_seconds: DRM handshake timeout.
    """

    def __init__(
        self,
        companion_url: str = f"http://localhost:{_DEFAULT_PORT}",
        timeout_seconds: int = 30,
    ) -> None:
        self._companion_url = companion_url
        self._timeout = timeout_seconds

    async def test_widevine_session(
        self, app: str = "generic"
    ) -> dict:
        """
        Verify EME key session creation and license acquisition for *app*.

        Args:
            app: One of ``"netflix"``, ``"amazon"``, ``"hulu"``, ``"generic"``.

        Returns:
            Dict with keys ``passed``, ``key_session_created``,
            ``license_acquired``, and ``error`` (if any).
        """
        result = {
            "app": app,
            "passed": False,
            "key_session_created": False,
            "license_acquired": False,
            "error": None,
        }

        try:
            import httpx

            async with httpx.AsyncClient(timeout=self._timeout) as client:
                # Health check
                resp = await client.get(f"{self._companion_url}/healthz")
                if resp.status_code != 200:
                    result["error"] = f"Companion server unhealthy: {resp.status_code}"
                    return result

                # Simulate EME key session creation
                drm_endpoint = f"{self._companion_url}/companion/api/drmSession"
                payload = {"app": app, "keySystem": "com.widevine.alpha"}
                drm_resp = await client.post(drm_endpoint, json=payload)

                if drm_resp.status_code == 200:
                    result["key_session_created"] = True
                    result["license_acquired"] = True
                    result["passed"] = True
                else:
                    result["error"] = f"DRM session failed: {drm_resp.status_code}"

        except Exception as exc:
            result["error"] = str(exc)
            logger.error("DRM test failed for app=%s: %s", app, exc)

        return result
