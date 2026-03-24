"""
SAFS v6.0 — Dynamic Companion Library Resolver

Queries a live Vizio TV via SSH to get the actual installed Companion Library
version and API schema at runtime.

Falls back to the static CompanionLibVersionMatrix when SSH is unavailable.

Usage:
    resolver = DynamicCompanionLibResolver(tv_ip="192.168.1.100", ssh_user="root")
    schema = await resolver.resolve()
    print(schema.version)  # "v2.1.0"
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from .version_matrix import CompanionApiSchema, CompanionLibVersionMatrix

logger = logging.getLogger(__name__)


class DynamicCompanionLibResolver:
    """
    Resolves Companion Library API schema by querying a live TV.

    Resolution strategy:
    1. SSH into TV and read /vendors/vizio/companion_lib/version.txt
    2. Parse version string → look up in CompanionLibVersionMatrix
    3. If SSH fails, fall back to static matrix using firmware version

    Real implementation requires ``asyncssh`` (optional dependency):
        pip install asyncssh
    """

    # Registry paths for version information
    VERSION_FILE = "/vendors/vizio/companion_lib/version.txt"
    FIRMWARE_FILE = "/etc/firmware_version"

    def __init__(
        self,
        tv_ip: Optional[str] = None,
        ssh_user: str = "root",
        ssh_password: Optional[str] = None,
        ssh_key_path: Optional[str] = None,
        ssh_port: int = 22,
        timeout: float = 10.0,
    ) -> None:
        """
        Args:
            tv_ip: TV IP address (None = offline mode, static only)
            ssh_user: SSH username
            ssh_password: SSH password (mutually exclusive with ssh_key_path)
            ssh_key_path: Path to SSH private key file
            ssh_port: SSH port
            timeout: Connection + command timeout seconds
        """
        self._tv_ip = tv_ip
        self._ssh_user = ssh_user
        self._ssh_password = ssh_password
        self._ssh_key_path = ssh_key_path
        self._ssh_port = ssh_port
        self._timeout = timeout
        self._matrix = CompanionLibVersionMatrix()

    async def resolve(
        self, fallback_firmware_version: Optional[str] = None
    ) -> CompanionApiSchema:
        """
        Resolve Companion Library API schema.

        Args:
            fallback_firmware_version: Firmware version to use if SSH fails

        Returns:
            CompanionApiSchema for the resolved version
        """
        if self._tv_ip is None:
            logger.debug("No TV IP configured — using static matrix fallback")
            if fallback_firmware_version:
                return self._matrix.get_schema_for_firmware(fallback_firmware_version)
            return self._matrix.all_schemas()[-1]

        # Try SSH query
        try:
            companion_version = await self._query_companion_version()
            if companion_version:
                schema = self._matrix.get_schema_by_companion_version(companion_version)
                if schema:
                    logger.info("Resolved Companion schema %s from TV", schema.version)
                    return schema

            # Try firmware version
            fw_version = await self._query_firmware_version()
            if fw_version:
                schema = self._matrix.get_schema_for_firmware(fw_version)
                logger.info(
                    "Resolved Companion schema %s from firmware %s",
                    schema.version,
                    fw_version,
                )
                return schema

        except Exception as exc:
            logger.warning("SSH query failed for %s: %s", self._tv_ip, exc)

        # Fall back to static
        fallback = fallback_firmware_version or "6.0.0"
        logger.info("Using static fallback for firmware %s", fallback)
        return self._matrix.get_schema_for_firmware(fallback)

    async def _query_companion_version(self) -> Optional[str]:
        """Query TV via SSH to get companion library version."""
        result = await self._ssh_exec(f"cat {self.VERSION_FILE}")
        if result:
            # Parse "v2.1.0" or "companion_lib_v2.1.0"
            match = re.search(r"v?(\d+\.\d+\.\d+)", result.strip())
            if match:
                return f"v{match.group(1)}"
        return None

    async def _query_firmware_version(self) -> Optional[str]:
        """Query TV via SSH to get firmware version."""
        result = await self._ssh_exec(f"cat {self.FIRMWARE_FILE}")
        if result:
            match = re.search(r"(\d+\.\d+\.\d+)", result.strip())
            if match:
                return match.group(1)
        return None

    async def _ssh_exec(self, command: str) -> Optional[str]:
        """Execute a command on the TV via SSH."""
        try:
            import asyncssh  # type: ignore[import]

            connect_kwargs: dict = {
                "host": self._tv_ip,
                "port": self._ssh_port,
                "username": self._ssh_user,
                "known_hosts": None,  # Skip host key checking for dev devices
                "connect_timeout": self._timeout,
            }

            if self._ssh_key_path:
                connect_kwargs["client_keys"] = [self._ssh_key_path]
            elif self._ssh_password:
                connect_kwargs["password"] = self._ssh_password

            async with asyncssh.connect(**connect_kwargs) as conn:  # type: ignore[attr-defined]
                result = await conn.run(command, timeout=self._timeout)
                if result.returncode == 0:
                    return result.stdout
        except ImportError:
            logger.debug("asyncssh not installed — cannot query TV via SSH")
        except Exception as exc:
            logger.debug("SSH exec failed for '%s': %s", command, exc)

        return None
