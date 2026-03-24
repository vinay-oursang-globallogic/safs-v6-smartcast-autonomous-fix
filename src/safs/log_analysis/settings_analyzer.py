"""
SAFS v6.0 — TV Settings Analyzer

Detects Vizio SmartCast configuration issues from log files, covering:
- Picture mode out-of-bounds
- Audio mode mismatches
- Network configuration errors
- Firmware version incompatibilities

Example usage::

    analyzer = SettingsAnalyzer()
    issues = analyzer.analyze(log_lines)
    for issue in issues:
        print(issue.category, issue.description)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ─── Issue patterns ────────────────────────────────────────────────────────────

@dataclass
class SettingsIssue:
    """
    A detected TV settings misconfiguration.

    Attributes:
        category: One of ``"picture"``, ``"audio"``, ``"network"``, ``"firmware"``.
        description: Human-readable description.
        severity: ``"critical"``, ``"high"``, ``"medium"``, or ``"low"``.
        log_match: The matched log message fragment.
        fix_suggestion: Short remediation advice.
    """

    category: str
    description: str
    severity: str
    log_match: str
    fix_suggestion: str = ""


_PICTURE_RULES: list[tuple[re.Pattern, str, str, str]] = [
    (re.compile(r"picture.*mode.*out.?of.?bounds|picture.*invalid.*mode", re.I), "Picture mode value out of bounds", "high", "Reset picture mode to Standard"),
    (re.compile(r"backlight.*\b(?:25[6-9]|[3-9]\d\d)\b", re.I), "Backlight level exceeds maximum", "medium", "Clamp backlight to 0–255"),
    (re.compile(r"hdr.*mode.*unsupported|unsupported.*hdr.*mode", re.I), "HDR mode not supported by display", "medium", "Disable HDR or upgrade firmware"),
    (re.compile(r"gamma.*calibrat.*fail|calibrat.*gamma.*error", re.I), "Gamma calibration failure", "high", "Re-run picture calibration"),
]

_AUDIO_RULES: list[tuple[re.Pattern, str, str, str]] = [
    (re.compile(r"audio.*mode.*mismatch|mismatch.*audio.*mode", re.I), "Audio mode mismatch between TV and amp", "medium", "Align audio output mode (e.g., Auto → PCM)"),
    (re.compile(r"dolby.*atmos.*unsupported|atmos.*not.*support", re.I), "Dolby Atmos not supported on current HDMI port", "low", "Use HDMI ARC for Atmos passthrough"),
    (re.compile(r"audio.*sync.*drift|av.*sync.*error", re.I), "Audio/video sync drift detected", "medium", "Adjust AV sync offset in audio settings"),
    (re.compile(r"sample.*rate.*mismatch|audio.*sample.*rate.*error", re.I), "Audio sample rate mismatch", "high", "Set audio output to Auto or 48 kHz"),
]

_NETWORK_RULES: list[tuple[re.Pattern, str, str, str]] = [
    (re.compile(r"DNS.*fail|unable.*resolve.*DNS", re.I), "DNS resolution failure", "high", "Check DNS server configuration"),
    (re.compile(r"wifi.*auth.*fail|WPA.*handshake.*fail|wifi.*connect.*fail", re.I), "Wi-Fi authentication failure", "critical", "Verify Wi-Fi password and security protocol"),
    (re.compile(r"IP.*address.*conflict|duplicate.*IP|ARP.*conflict", re.I), "IP address conflict detected", "high", "Enable DHCP or assign static non-conflicting IP"),
    (re.compile(r"MTU.*mismatch|packet.*too.*large", re.I), "MTU mismatch causing packet fragmentation", "medium", "Lower MTU to 1400 for VPN/PPTP connections"),
    (re.compile(r"NTP.*sync.*fail|time.*sync.*error", re.I), "NTP time synchronization failure", "medium", "Configure NTP server address in network settings"),
]

_FIRMWARE_RULES: list[tuple[re.Pattern, str, str, str]] = [
    (re.compile(r"firmware.*version.*incompatible|incompatible.*firmware", re.I), "Firmware version incompatibility detected", "critical", "Upgrade or downgrade firmware to compatible version"),
    (re.compile(r"factory.*reset.*required|need.*factory.*reset", re.I), "Factory reset required after firmware change", "high", "Perform factory reset to clear invalid settings"),
    (re.compile(r"bootloader.*version.*mismatch", re.I), "Bootloader version mismatch", "critical", "Reflash bootloader to matching version"),
    (re.compile(r"OTA.*version.*downgrade|downgrade.*OTA", re.I), "OTA downgrade attempt detected", "high", "Prevent OTA downgrade – verify target firmware version"),
]

_ALL_RULES: list[tuple[str, list[tuple[re.Pattern, str, str, str]]]] = [
    ("picture", _PICTURE_RULES),
    ("audio", _AUDIO_RULES),
    ("network", _NETWORK_RULES),
    ("firmware", _FIRMWARE_RULES),
]


class SettingsAnalyzer:
    """
    Analyze log lines for Vizio SmartCast settings-related issues.

    Each rule is applied per line; the first matching rule wins for that line.
    """

    def analyze(self, log_lines: list[str]) -> list[SettingsIssue]:
        """
        Scan *log_lines* for TV settings issues.

        Args:
            log_lines: Raw log lines (not yet enriched with timestamps).

        Returns:
            List of :class:`SettingsIssue` sorted by severity (critical first).
        """
        issues: list[SettingsIssue] = []
        seen_descriptions: set[str] = set()

        for line in log_lines:
            for category, rules in _ALL_RULES:
                for pattern, description, severity, fix in rules:
                    if pattern.search(line):
                        if description not in seen_descriptions:
                            seen_descriptions.add(description)
                            issues.append(
                                SettingsIssue(
                                    category=category,
                                    description=description,
                                    severity=severity,
                                    log_match=line[:200],
                                    fix_suggestion=fix,
                                )
                            )
                        break

        _SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        issues.sort(key=lambda i: _SEVERITY_ORDER.get(i.severity, 4))
        return issues
