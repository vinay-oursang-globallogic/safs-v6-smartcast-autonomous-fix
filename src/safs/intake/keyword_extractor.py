"""
SAFS v6.0 — Keyword Extractor

Extracts technical keywords and context from Jira ticket descriptions
for use in:
- Log analysis seed terms (SmartTVErrorAnalyzer context)
- Retrieval query expansion (ContextBuilder)
- BugLayerRouter signal boosting

Uses a combination of:
1. Rule-based mapping (freeze → deadlock, hang, timeout)
2. Component name recognition (loki, chromium, vdec, mali)
3. App name detection (netflix, hulu, amazon)
4. Error code recognition (SIGSEGV, OOM, HDCP)
"""

from __future__ import annotations

import re
from typing import Optional


# ---------------------------------------------------------------------------
# Component / subsystem keyword tables
# ---------------------------------------------------------------------------

_COMPONENT_TERMS: dict[str, list[str]] = {
    # Stability → technical failures
    "freeze": ["deadlock", "hang", "timeout", "livelock"],
    "crash": ["segfault", "sigsegv", "null_deref", "abort", "terminate"],
    "hang": ["deadlock", "mutex_lock", "timeout", "blocked"],
    "stuck": ["deadlock", "hang", "blocking"],
    "unresponsive": ["hang", "deadlock", "cpu_spin"],
    "not responding": ["hang", "deadlock", "watchdog"],
    # Display
    "black screen": ["framebuffer_fail", "display_init", "hdmi_handshake", "vdec_fail"],
    "no picture": ["video_fail", "vdec", "framebuffer", "display_off"],
    "flickering": ["vsync", "framebuffer", "gpu_hang", "tearing"],
    "distorted": ["codec_error", "bitrate", "decode_artifact"],
    # Audio
    "no sound": ["audio_fail", "adsp_crash", "codec_init_fail", "dsp_error"],
    "no audio": ["audio_fail", "adsp", "codec"],
    "mute": ["audio_mute", "adsp_fail"],
    # Network
    "buffering": ["network_timeout", "bandwidth_limit", "cdn_fail", "buffer_underrun"],
    "not loading": ["network_timeout", "fetch_fail", "dns_fail", "connection_refused"],
    "slow": ["latency_high", "timeout", "packet_loss"],
    "loading": ["fetch_timeout", "manifest_parse", "network_error"],
    # Boot / power
    "reboot": ["spontaneous_reboot", "watchdog_reset", "kernel_panic"],
    "not turning on": ["boot_fail", "power_management", "init_fail"],
    "slow boot": ["init_slow", "service_timeout"],
    # Remote / input
    "remote": ["ir_routing", "keydown_event", "hid_fail"],
    "button": ["keydown", "ir_event", "input_fail"],
    "not working": ["input_fail", "service_crash", "init_fail"],
    # DRM / content protection
    "drm": ["widevine_l1", "hdcp_2_2", "eme_error", "drm_license"],
    "protected content": ["hdcp", "widevine", "security_fail"],
    "license": ["drm_license_fail", "widevine_license", "eme_fail"],
    # Memory
    "memory": ["oom", "malloc_fail", "heap_corrupt"],
    "out of memory": ["oom", "malloc_enobufs", "heap_exhausted"],
    "memory leak": ["leak", "event_listener", "heap_growth"],
}

# Exact component names found in Vizio logs (pass through directly)
_COMPONENT_DIRECT = {
    "loki", "chromium", "vdec", "mali", "adsp", "mmc",
    "widevine", "hdcp", "eme", "ir", "watchdog", "directfb",
    "opengl", "gles", "surfaceflinger", "mediatek", "mtk",
    "netflix", "hulu", "amazon", "youtube", "pluto", "watchfree",
    "asan", "tsan", "ubsan", "oom",
}

# Error codes / signals → keywords
_ERROR_CODE_MAP: dict[re.Pattern[str], list[str]] = {
    re.compile(r"SIGSEGV|signal 11", re.I): ["sigsegv", "null_deref", "segfault"],
    re.compile(r"SIGABRT|signal 6", re.I): ["abort", "assert_fail"],
    re.compile(r"SIGBUS|signal 7", re.I): ["sigbus", "misaligned_access"],
    re.compile(r"OOM|out.of.memory", re.I): ["oom", "malloc_fail"],
    re.compile(r"HDCP"): ["hdcp_fail", "drm_fail"],
    re.compile(r"widevine", re.I): ["widevine", "drm_fail"],
    re.compile(r"mt5396|mt5670|mt5882", re.I): ["mediatek", "chipset"],
    re.compile(r"addr2line|backtrace|tombstone", re.I): ["crash", "native_crash"],
    re.compile(r"I/O error|EIO", re.I): ["io_error", "mmc_fail"],
}


class KeywordExtractor:
    """
    Extracts technical context keywords from raw Jira ticket text.

    Call `extract(description)` to get a deduplicated list of lowercase
    technical terms suitable for use as search seeds / analysis context.
    """

    def extract(self, text: str) -> list[str]:
        """
        Extract technical keywords from a Jira ticket description.

        Args:
            text: Raw description text (may include ADF plain-text)

        Returns:
            Deduplicated list of lowercase technical keyword strings
        """
        keywords: set[str] = set()
        text_lower = text.lower()

        # 1. Rule-based phrase mapping
        for phrase, tech_terms in _COMPONENT_TERMS.items():
            if phrase in text_lower:
                keywords.update(tech_terms)

        # 2. Direct component name matching
        words = re.findall(r"\b\w+\b", text_lower)
        for word in words:
            if word in _COMPONENT_DIRECT:
                keywords.add(word)

        # 3. Error codes / signals
        for pattern, terms in _ERROR_CODE_MAP.items():
            if pattern.search(text):
                keywords.update(terms)

        # 4. Quoted strings (file names, function names in backticks / quotes)
        for quoted in re.findall(r'["`]([^"`]{3,50})["`]', text):
            # Only include if plausible code identifier
            if re.match(r"^[\w:/\-.]+$", quoted):
                keywords.add(quoted.lower())

        # 5. P0-P4 priority mentioned inline → add to context
        if re.search(r"\bP0\b|\bP1\b", text):
            keywords.add("critical")

        return sorted(keywords)

    def extract_from_ticket(
        self,
        summary: str,
        description: str,
        labels: Optional[list[str]] = None,
    ) -> list[str]:
        """
        Extract keywords from all text fields of a Jira ticket.

        Args:
            summary: Ticket summary line
            description: Ticket description body
            labels: Optional list of Jira labels

        Returns:
            Deduplicated sorted list of technical keywords
        """
        combined_text = f"{summary} {description}"
        if labels:
            combined_text += " " + " ".join(labels)
        return self.extract(combined_text)
