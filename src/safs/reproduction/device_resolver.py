"""
Dynamic Companion Library Version Resolver
===========================================

Queries live TV system registry via vizio-ssh MCP server to determine
exact Companion Library version, LOKi version, firmware version, and chipset.

Replaces static CompanionLibVersionMatrix with runtime resolution.

Master Prompt Reference: Section 3.8, Part 1 (Improvement 6)
"""

import logging
import re
from typing import Optional

from .models import CompanionLibInfo

logger = logging.getLogger(__name__)


class DynamicCompanionLibResolver:
    """
    Resolves Companion Library information from live TV system registry.
    
    Queries paths via vizio-ssh MCP server:
    - /app/loki/version → LOKi version
    - /os/version/firmware → Firmware version
    - /hw/chipset/model → Chipset model
    - /app/loki/config/companion-server-enabled → Companion enabled
    - /app/cobalt/version → Chromium/Cobalt version
    
    Derives Companion API version from LOKi version (e.g., "3.2.1" → "v3.2").
    """
    
    def __init__(self, ssh_client):
        """
        Initialize resolver with vizio-ssh MCP client.
        
        Args:
            ssh_client: vizio-ssh MCP client instance with .call() method
        """
        self.ssh = ssh_client
    
    async def resolve(self) -> CompanionLibInfo:
        """
        Query live TV registry and build CompanionLibInfo.
        
        Returns:
            CompanionLibInfo with live system information
            
        Raises:
            RuntimeError: If required registry values cannot be read
        """
        logger.info("Resolving companion library info from live TV registry...")
        
        try:
            # Query all required registry values
            loki_version = await self._get_registry_value("/app/loki/version")
            firmware_version = await self._get_registry_value("/os/version/firmware")
            chipset = await self._get_registry_value("/hw/chipset/model")
            companion_enabled_str = await self._get_registry_value(
                "/app/loki/config/companion-server-enabled"
            )
            chromium_version = await self._get_registry_value(
                "/app/cobalt/version", default=None
            )
            
            # Parse companion enabled flag
            companion_enabled = companion_enabled_str.lower() in ("true", "1", "yes")
            
            # Derive Companion API version from LOKi version
            companion_api_version = self._derive_api_version(loki_version)
            
            info = CompanionLibInfo(
                loki_version=loki_version,
                firmware_version=firmware_version,
                chipset=chipset,
                companion_enabled=companion_enabled,
                chromium_version=chromium_version,
                companion_api_version=companion_api_version,
            )
            
            logger.info(
                f"Resolved companion info: LOKi={loki_version}, "
                f"FW={firmware_version}, Chipset={chipset}, API={companion_api_version}"
            )
            
            return info
            
        except Exception as e:
            logger.error(f"Failed to resolve companion library info: {e}")
            raise RuntimeError(f"Cannot resolve companion library info: {e}") from e
    
    async def _get_registry_value(
        self, path: str, default: Optional[str] = ""
    ) -> str:
        """
        Get registry value via vizio-ssh MCP server.
        
        Args:
            path: Registry path (e.g., "/app/loki/version")
            default: Default value if path not found (None = raise error)
            
        Returns:
            Registry value as string
            
        Raises:
            RuntimeError: If default=None and value not found
        """
        try:
            result = await self.ssh.call("get_registry_value", path=path)
            
            # Handle different return formats
            if isinstance(result, dict) and "value" in result:
                value = result["value"]
            elif isinstance(result, str):
                value = result
            else:
                if default is None:
                    raise RuntimeError(f"Registry value not found: {path}")
                value = default
            
            return value.strip() if value else value
            
        except Exception as e:
            if default is None:
                raise RuntimeError(f"Failed to read registry {path}: {e}") from e
            logger.warning(f"Registry value not found {path}, using default: {default}")
            return default
    
    def _derive_api_version(self, loki_version: str) -> str:
        """
        Derive Companion API version from LOKi version.
        
        Examples:
        - "3.2.1" → "v3.2"
        - "4.0.5" → "v4.0"
        - "2.8.3-beta" → "v2.8"
        
        Args:
            loki_version: LOKi version string
            
        Returns:
            Companion API version (e.g., "v3.2")
        """
        # Extract major.minor from LOKi version
        match = re.match(r"(\d+)\.(\d+)", loki_version)
        
        if match:
            major, minor = match.groups()
            return f"v{major}.{minor}"
        else:
            logger.warning(
                f"Could not parse LOKi version '{loki_version}', "
                "defaulting to v3.0"
            )
            return "v3.0"
    
    def check_firmware_compatible(
        self, tv_firmware: str, ticket_firmware: Optional[str]
    ) -> bool:
        """
        Check if TV firmware version is compatible with ticket firmware.
        
        Compatible if:
        - Exact match
        - Same major.minor version
        - ticket_firmware is None (no requirement)
        
        Args:
            tv_firmware: Firmware version on TV (e.g., "5.2.1")
            ticket_firmware: Firmware version in ticket (e.g., "5.2.0")
            
        Returns:
            True if compatible, False otherwise
        """
        if not ticket_firmware:
            # No firmware requirement in ticket
            return True
        
        # Exact match
        if tv_firmware == ticket_firmware:
            return True
        
        # Extract major.minor versions
        tv_match = re.match(r"(\d+)\.(\d+)", tv_firmware)
        ticket_match = re.match(r"(\d+)\.(\d+)", ticket_firmware)
        
        if tv_match and ticket_match:
            tv_major_minor = tv_match.groups()
            ticket_major_minor = ticket_match.groups()
            
            # Same major.minor → compatible
            if tv_major_minor == ticket_major_minor:
                return True
        
        # Not compatible
        logger.warning(
            f"Firmware mismatch: TV={tv_firmware}, Ticket={ticket_firmware}"
        )
        return False


__all__ = ["DynamicCompanionLibResolver"]
