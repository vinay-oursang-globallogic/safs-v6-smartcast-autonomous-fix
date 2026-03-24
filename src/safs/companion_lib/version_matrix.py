"""
SAFS v6.0 — Companion Library Version Matrix

Static lookup table mapping LOKi build versions to Companion Library API
schema versions. Used when the TV is not reachable (offline / pre-production).

Companion Library API schema determines:
- JavaScript event names (VIZIO_LIBRARY_DID_LOAD vs COMPANION_READY)
- Method signatures (getVersion vs getLibraryVersion)
- Message protocol (WS vs postMessage)
- IR routing table key names
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CompanionApiSchema:
    """API schema for a specific Companion Library version."""

    version: str  # e.g. "v2.1.0"
    ready_event: str  # JS event fired when library is loaded
    version_method: str  # How to get library version
    ir_key_table: str  # IR routing table key prefix
    uses_websocket: bool  # True for WS protocol, False for postMessage
    min_firmware: str  # Minimum firmware version
    max_firmware: Optional[str] = None
    notes: str = ""


# ---------------------------------------------------------------------------
# Static Version Matrix
# ---------------------------------------------------------------------------

_VERSION_TABLE: list[tuple[str, str, CompanionApiSchema]] = [
    # (loki_min, loki_max_exclusive, schema)
    (
        "5.0.0",
        "5.5.0",
        CompanionApiSchema(
            version="v1.8.0",
            ready_event="COMPANION_READY",
            version_method="getVersion()",
            ir_key_table="legacy_ir_keys",
            uses_websocket=False,
            min_firmware="5.0.0",
            max_firmware="5.4.99",
            notes="Legacy postMessage protocol",
        ),
    ),
    (
        "5.5.0",
        "5.9.0",
        CompanionApiSchema(
            version="v1.9.0",
            ready_event="VIZIO_LIBRARY_DID_LOAD",
            version_method="getLibraryVersion()",
            ir_key_table="ir_key_v2",
            uses_websocket=False,
            min_firmware="5.5.0",
            max_firmware="5.8.99",
            notes="Renamed load event, updated IR table",
        ),
    ),
    (
        "5.9.0",
        "6.0.0",
        CompanionApiSchema(
            version="v2.0.0",
            ready_event="VIZIO_LIBRARY_DID_LOAD",
            version_method="getLibraryVersion()",
            ir_key_table="ir_key_v3",
            uses_websocket=True,
            min_firmware="5.9.0",
            max_firmware="5.99.99",
            notes="WebSocket protocol introduced",
        ),
    ),
    (
        "6.0.0",
        "99.0.0",
        CompanionApiSchema(
            version="v2.1.0",
            ready_event="VIZIO_LIBRARY_DID_LOAD",
            version_method="getLibraryVersion()",
            ir_key_table="ir_key_v3",
            uses_websocket=True,
            min_firmware="6.0.0",
            max_firmware=None,
            notes="Current stable API",
        ),
    ),
]


def _version_tuple(v: str) -> tuple[int, ...]:
    """Convert version string to comparable tuple."""
    try:
        return tuple(int(x) for x in v.split(".")[:3])
    except ValueError:
        return (0, 0, 0)


class CompanionLibVersionMatrix:
    """
    Static lookup of Companion Library API schema from LOKi/firmware version.

    Usage:
        matrix = CompanionLibVersionMatrix()
        schema = matrix.get_schema_for_firmware("5.10.22")
        print(schema.ready_event)  # "VIZIO_LIBRARY_DID_LOAD"
    """

    def get_schema_for_firmware(self, firmware_version: str) -> CompanionApiSchema:
        """
        Get API schema for a given firmware version.

        Args:
            firmware_version: Firmware version string (e.g., "5.10.22.1")

        Returns:
            CompanionApiSchema for the closest matching version range
        """
        ver = _version_tuple(firmware_version)
        for loki_min, loki_max, schema in _VERSION_TABLE:
            min_t = _version_tuple(loki_min)
            max_t = _version_tuple(loki_max)
            if min_t <= ver < max_t:
                return schema

        # Default: return latest schema
        return _VERSION_TABLE[-1][2]

    def get_schema_by_companion_version(
        self, companion_version: str
    ) -> Optional[CompanionApiSchema]:
        """
        Look up schema by explicit Companion Library version string.

        Args:
            companion_version: Companion version (e.g., "v2.1.0")

        Returns:
            CompanionApiSchema or None if not found
        """
        for _, _, schema in _VERSION_TABLE:
            if schema.version == companion_version:
                return schema
        return None

    def all_schemas(self) -> list[CompanionApiSchema]:
        """Return all known API schemas sorted by version."""
        return [schema for _, _, schema in _VERSION_TABLE]
