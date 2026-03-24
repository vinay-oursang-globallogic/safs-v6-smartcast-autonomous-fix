"""
SAFS v6.0 — Enriched Error Patterns Library

Provides 100+ compiled regex patterns for matching Vizio SmartCast log errors,
covering all 27 error categories defined in the Master Prompt Part Two.

Each pattern is an :class:`EnrichedErrorPattern` dataclass that carries:
- Pre-compiled regex
- Severity and bug layer assignment
- Cross-layer hint for ``CROSS_LAYER`` routing
- Fix hint for the fix generator
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from safs.log_analysis.models import BugLayer, ErrorCategory


@dataclass
class EnrichedErrorPattern:
    """
    A single enriched error pattern.

    Attributes:
        id: Unique string identifier.
        name: Human-readable name.
        compiled_regex: Pre-compiled pattern for fast matching.
        severity: ``"critical"``, ``"high"``, ``"medium"``, or ``"low"``.
        bug_layer: Primary :class:`BugLayer` for routing.
        error_category: Specific :class:`ErrorCategory`.
        bug_layer_confidence: Float in [0.0, 1.0] for routing confidence.
        cross_layer_hint: ``True`` if this signal often accompanies
            another layer's error.
        description: Short human description.
        fix_hint: One-line guidance for the fix generator.
    """

    id: str
    name: str
    compiled_regex: re.Pattern
    severity: str
    bug_layer: BugLayer
    error_category: ErrorCategory
    bug_layer_confidence: float = 0.9
    cross_layer_hint: bool = False
    description: str = ""
    fix_hint: str = ""


def _p(
    pid: str,
    name: str,
    pattern: str,
    severity: str,
    layer: BugLayer,
    category: ErrorCategory,
    confidence: float = 0.9,
    cross: bool = False,
    description: str = "",
    fix_hint: str = "",
) -> EnrichedErrorPattern:
    """Helper to create an :class:`EnrichedErrorPattern`."""
    return EnrichedErrorPattern(
        id=pid,
        name=name,
        compiled_regex=re.compile(pattern, re.IGNORECASE),
        severity=severity,
        bug_layer=layer,
        error_category=category,
        bug_layer_confidence=confidence,
        cross_layer_hint=cross,
        description=description,
        fix_hint=fix_hint,
    )


_L = BugLayer
_EC = ErrorCategory


# ─────────────────────────────────────────────────────────────────────────────
# LOKi Native C++ (8 categories)
# ─────────────────────────────────────────────────────────────────────────────
_LOKI_PATTERNS: list[EnrichedErrorPattern] = [
    # LOKI_SEGFAULT_NULL_DEREF
    _p("L001", "LOKi SIGSEGV", r"signal 11 .SIGSEGV", "critical", _L.LOKI, _EC.LOKI_SEGFAULT_NULL_DEREF, 0.97, description="LOKi process received SIGSEGV", fix_hint="Check null pointer dereference in crash frame 0"),
    _p("L002", "LOKi null deref address", r"Fault addr:?\s+0x0+\b", "critical", _L.LOKI, _EC.LOKI_SEGFAULT_NULL_DEREF, 0.95, fix_hint="Null pointer dereference – add nullptr guard"),
    _p("L003", "LOKi backtrace frame 0", r"#00 pc [0-9a-f]+ .*/loki", "high", _L.LOKI, _EC.LOKI_SEGFAULT_NULL_DEREF, 0.85, fix_hint="Examine #00 call site for null reference"),
    _p("L004", "Android tombstone SIGSEGV", r"signal 11 \(SIGSEGV\).*code 1 \(SEGV_MAPERR\)", "critical", _L.LOKI, _EC.LOKI_SEGFAULT_NULL_DEREF, 0.97, fix_hint="Unmapped address access"),
    _p("L005", "LOKi crash abort", r"SIGABRT.*loki", "critical", _L.LOKI, _EC.LOKI_SEGFAULT_NULL_DEREF, 0.9, fix_hint="Check SIGABRT source – likely abort() or assert()"),
    # LOKI_MEMORY_CORRUPTION
    _p("L006", "AddressSanitizer heap overflow", r"heap-buffer-overflow", "critical", _L.LOKI, _EC.LOKI_MEMORY_CORRUPTION, 0.99, fix_hint="Bounds check needed at flagged alloc site"),
    _p("L007", "AddressSanitizer use-after-free", r"heap-use-after-free", "critical", _L.LOKI, _EC.LOKI_MEMORY_CORRUPTION, 0.99, fix_hint="Lifetime issue – use shared_ptr or weak_ptr"),
    _p("L008", "AddressSanitizer stack overflow", r"stack-buffer-overflow", "critical", _L.LOKI, _EC.LOKI_MEMORY_CORRUPTION, 0.99, fix_hint="Stack buffer too small – increase or use heap"),
    _p("L009", "AddressSanitizer double-free", r"double-free", "critical", _L.LOKI, _EC.LOKI_MEMORY_CORRUPTION, 0.99, fix_hint="Track ownership – use RAII or unique_ptr"),
    _p("L010", "Memory corruption canary", r"stack canary .* detected", "critical", _L.LOKI, _EC.LOKI_MEMORY_CORRUPTION, 0.95, fix_hint="Stack smash – check array write bounds"),
    # LOKI_RACE_CONDITION
    _p("L011", "ThreadSanitizer data race", r"DATA RACE", "high", _L.LOKI, _EC.LOKI_RACE_CONDITION, 0.99, fix_hint="Add mutex/lock around flagged shared access"),
    _p("L012", "ThreadSanitizer race location", r"ThreadSanitizer: data race", "high", _L.LOKI, _EC.LOKI_RACE_CONDITION, 0.99, fix_hint="Synchronise flagged shared variable"),
    _p("L013", "LOKi mutex deadlock", r"mutex.*deadlock|deadlock.*mutex", "high", _L.LOKI, _EC.LOKI_RACE_CONDITION, 0.85, fix_hint="Break deadlock – use lock order or try_lock"),
    _p("L014", "LOKi thread timeout", r"thread.*timed? ?out.*loki", "medium", _L.LOKI, _EC.LOKI_RACE_CONDITION, 0.7, fix_hint="Increase timeout or unblock waiting thread"),
    # LOKI_APP_LAUNCH_FAILURE
    _p("L015", "LOKi AppLauncher failure", r"AppLauncher.*fail|launch.*failed.*LOKi", "high", _L.LOKI, _EC.LOKI_APP_LAUNCH_FAILURE, 0.9, fix_hint="Check AppLauncher::launch() error path"),
    _p("L016", "LOKi app start timeout", r"app.*start.*timeout|timeout.*starting.*app", "high", _L.LOKI, _EC.LOKI_APP_LAUNCH_FAILURE, 0.85, fix_hint="Increase launch timeout or fix blocking init"),
    _p("L017", "AppRegistry missing entry", r"AppRegistry.*not found|no entry.*AppRegistry", "medium", _L.LOKI, _EC.LOKI_APP_LAUNCH_FAILURE, 0.8, fix_hint="Register app in AppRegistry manifest"),
    _p("L018", "LOKi app process died", r"Process.*died.*app|app.*process.*killed", "high", _L.LOKI, _EC.LOKI_APP_LAUNCH_FAILURE, 0.85, fix_hint="Check app crash logs"),
    # LOKI_IR_ROUTING_FAILURE
    _p("L019", "IR key routing failure", r"IR.*rout.*fail|key.*dispatch.*fail", "high", _L.LOKI, _EC.LOKI_IR_ROUTING_FAILURE, 0.9, fix_hint="Verify IR key table mappings"),
    _p("L020", "IR receiver timeout", r"IR.*receiver.*timeout|timeout.*IR", "medium", _L.LOKI, _EC.LOKI_IR_ROUTING_FAILURE, 0.8, fix_hint="Check IR receiver hardware connection"),
    _p("L021", "Key event drop", r"key.*event.*drop|drop.*key.*event", "medium", _L.LOKI, _EC.LOKI_IR_ROUTING_FAILURE, 0.75, fix_hint="Increase key event queue depth"),
    # LOKI_COMPANION_SERVER_DEADLOCK
    _p("L022", "Companion server deadlock", r"companion.*server.*deadlock|deadlock.*companion", "critical", _L.LOKI, _EC.LOKI_COMPANION_SERVER_DEADLOCK, 0.95, cross=True, fix_hint="Break companion server lock cycle"),
    _p("L023", "Companion server timeout", r"companion.*server.*timeout|CompanionServer.*block", "high", _L.LOKI, _EC.LOKI_COMPANION_SERVER_DEADLOCK, 0.85, cross=True, fix_hint="Add async timeout to companion server calls"),
    _p("L024", "VIZIO library did not load", r"VIZIO_LIBRARY_DID_LOAD.*timeout|companion.*library.*timeout", "high", _L.LOKI, _EC.LOKI_COMPANION_SERVER_DEADLOCK, 0.8, cross=True, fix_hint="Fix companion library load race"),
    # LOKI_EPG_PARSE_ERROR
    _p("L025", "EPG parse failure", r"EPG.*parse.*error|parse.*EPG.*fail", "medium", _L.LOKI, _EC.LOKI_EPG_PARSE_ERROR, 0.9, fix_hint="Validate EPG XML schema"),
    _p("L026", "EPG data malformed", r"malformed.*EPG|EPG.*malform", "medium", _L.LOKI, _EC.LOKI_EPG_PARSE_ERROR, 0.85, fix_hint="Add EPG schema validation"),
    _p("L027", "EPG stream read error", r"EPG.*stream.*eof|eof.*EPG", "low", _L.LOKI, _EC.LOKI_EPG_PARSE_ERROR, 0.7, fix_hint="Retry EPG fetch on EOF"),
    # LOKI_OTA_UPDATE_FAILURE
    _p("L028", "OTA download failure", r"OTA.*download.*fail|fail.*OTA.*download", "high", _L.LOKI, _EC.LOKI_OTA_UPDATE_FAILURE, 0.9, fix_hint="Implement OTA download retry with backoff"),
    _p("L029", "OTA verification failure", r"OTA.*verif.*fail|signature.*OTA.*invalid", "critical", _L.LOKI, _EC.LOKI_OTA_UPDATE_FAILURE, 0.95, fix_hint="Check OTA signing key and hash"),
    _p("L030", "OTA flash failure", r"OTA.*flash.*fail|flash.*fail.*OTA", "critical", _L.LOKI, _EC.LOKI_OTA_UPDATE_FAILURE, 0.9, fix_hint="Check storage space before OTA flash"),
]

# ─────────────────────────────────────────────────────────────────────────────
# HTML5 Streaming Apps (13 categories)
# ─────────────────────────────────────────────────────────────────────────────
_HTML5_PATTERNS: list[EnrichedErrorPattern] = [
    # COMPANION_LIB_TIMING
    _p("H001", "VIZIO_LIBRARY_DID_LOAD event missing", r"VIZIO_LIBRARY_DID_LOAD.*not.*fired|waiting.*VIZIO_LIBRARY_DID_LOAD", "high", _L.HTML5, _EC.COMPANION_LIB_TIMING, 0.95, cross=True, fix_hint="Fire VIZIO_LIBRARY_DID_LOAD after companion init"),
    _p("H002", "Companion lib load timeout", r"window\.VIZIO.*undefined|VIZIO.*not defined", "high", _L.HTML5, _EC.COMPANION_LIB_TIMING, 0.9, fix_hint="Check companion lib injection timing"),
    _p("H003", "Companion lib API race", r"companion.*API.*race|race.*companion.*API", "high", _L.HTML5, _EC.COMPANION_LIB_TIMING, 0.85, cross=True, fix_hint="Await VIZIO_LIBRARY_DID_LOAD before API calls"),
    _p("H004", "Companion server not ready", r"CompanionServer.*not ready|companion.*server.*unavail", "medium", _L.HTML5, _EC.COMPANION_LIB_TIMING, 0.8, cross=True, fix_hint="Retry companion server connection"),
    # JS_HEAP_OOM
    _p("H005", "JavaScript heap OOM", r"javascript heap out of memory|JS Heap.*OOM", "critical", _L.HTML5, _EC.JS_HEAP_OOM, 0.99, fix_hint="Identify memory leak; use WeakRef or finalizers"),
    _p("H006", "Chromium OOM killer", r"OOM.*Chromium|chromium.*killed.*OOM|renderer.*OOM", "critical", _L.HTML5, _EC.JS_HEAP_OOM, 0.95, fix_hint="Reduce renderer memory pressure"),
    _p("H007", "Memory pressure listener", r"onmemorypressure|memoryPressure.*critical", "high", _L.HTML5, _EC.JS_HEAP_OOM, 0.85, fix_hint="Release caches on memory pressure event"),
    # EME_DRM_FAILURE
    _p("H008", "EME key session error", r"createMediaKeys.*fail|MediaKeySession.*error", "high", _L.HTML5, _EC.EME_DRM_FAILURE, 0.9, fix_hint="Retry EME key session creation"),
    _p("H009", "DRM license failure", r"license.*request.*fail|DRM.*license.*error", "high", _L.HTML5, _EC.EME_DRM_FAILURE, 0.9, fix_hint="Check license server URL and auth token"),
    _p("H010", "Widevine decrypt error", r"widevine.*decrypt.*error|DECRYPT.*WIDEVINE", "critical", _L.HTML5, _EC.EME_DRM_FAILURE, 0.95, fix_hint="Verify Widevine CDM version compatibility"),
    # KEYDOWN_NOT_FIRED
    _p("H011", "Keydown event not fired", r"keydown.*not.*fired|missing.*keydown.*event", "high", _L.HTML5, _EC.KEYDOWN_NOT_FIRED, 0.9, fix_hint="Check key event routing in focus manager"),
    _p("H012", "Focus management failure", r"focus.*trap|lost.*focus|focus.*not.*set", "medium", _L.HTML5, _EC.KEYDOWN_NOT_FIRED, 0.75, fix_hint="Restore focus to interactive element"),
    _p("H013", "Key event preventDefault", r"preventDefault.*key|key.*event.*cancelled", "medium", _L.HTML5, _EC.KEYDOWN_NOT_FIRED, 0.7, fix_hint="Check for erroneous preventDefault() calls"),
    # FETCH_NETWORK_TIMEOUT
    _p("H014", "Fetch network timeout", r"fetch.*timeout|network.*timeout.*fetch", "high", _L.HTML5, _EC.FETCH_NETWORK_TIMEOUT, 0.9, fix_hint="Add AbortController with timeout to fetch()"),
    _p("H015", "XMLHttpRequest timeout", r"xhr.*timeout|XMLHttpRequest.*timeout", "medium", _L.HTML5, _EC.FETCH_NETWORK_TIMEOUT, 0.85, fix_hint="Set xhr.timeout and handle ontimeout"),
    _p("H016", "Network offline", r"navigator\.onLine.*false|offline.*network", "medium", _L.HTML5, _EC.FETCH_NETWORK_TIMEOUT, 0.75, fix_hint="Handle navigator.onLine=false gracefully"),
    # SHAKA_ERROR_3016
    _p("H017", "Shaka 3016 MSE seek", r"Shaka.*3016|MEDIA_SOURCE_MUTUALLY_EXCLUSIVE", "high", _L.HTML5, _EC.SHAKA_ERROR_3016, 0.99, fix_hint="Prevent concurrent MSE segment appends"),
    _p("H018", "Shaka MSE error", r"shaka.*media.source.*error|MSE.*shaka", "high", _L.HTML5, _EC.SHAKA_ERROR_3016, 0.9, fix_hint="Reset MediaSource on Shaka MSE error"),
    _p("H019", "Shaka DASH manifest error", r"shaka.*DASH.*manifest.*error|manifest.*shaka.*fail", "medium", _L.HTML5, _EC.SHAKA_ERROR_3016, 0.85, fix_hint="Validate DASH manifest URL and headers"),
    # NETFLIX_MSL_TIMEOUT
    _p("H020", "Netflix MSL timeout", r"MSL.*timeout|netflix.*msl.*timeout", "high", _L.HTML5, _EC.NETFLIX_MSL_TIMEOUT, 0.95, fix_hint="Retry Netflix MSL handshake"),
    _p("H021", "Netflix nfp error", r"nfp\.error|netflix.*playback.*error", "high", _L.HTML5, _EC.NETFLIX_MSL_TIMEOUT, 0.9, fix_hint="Check Netflix app version for Chromium compat"),
    _p("H022", "Netflix auth failure", r"netflix.*auth.*fail|netflix.*401", "high", _L.HTML5, _EC.NETFLIX_MSL_TIMEOUT, 0.85, fix_hint="Refresh Netflix device certificates"),
    # AMAZON_DASH_MANIFEST
    _p("H023", "Amazon DASH manifest error", r"amazon.*DASH.*manifest|dash\.js.*amazon.*error", "high", _L.HTML5, _EC.AMAZON_DASH_MANIFEST, 0.9, fix_hint="Update Amazon DASH manifest parsing"),
    _p("H024", "Amazon video decode failure", r"amazon.*video.*decode.*fail|decode.*error.*amazon", "high", _L.HTML5, _EC.AMAZON_DASH_MANIFEST, 0.85, fix_hint="Check codec support for Amazon streams"),
    # HULU_AD_MSE_BREAK
    _p("H025", "Hulu ad MSE break", r"hulu.*ad.*MSE.*break|MSE.*break.*hulu", "high", _L.HTML5, _EC.HULU_AD_MSE_BREAK, 0.95, fix_hint="Handle MSE SourceBuffer abort() on ad break"),
    _p("H026", "Hulu VideoJS error", r"videojs.*hulu.*error|hulu.*videojs.*error", "medium", _L.HTML5, _EC.HULU_AD_MSE_BREAK, 0.85, fix_hint="Update VideoJS ad plugin"),
    # WATCHFREE_DEEPLINK_LOSS
    _p("H027", "WatchFree deeplink loss", r"WatchFree.*deeplink.*loss|deeplink.*WatchFree.*fail", "high", _L.HTML5, _EC.WATCHFREE_DEEPLINK_LOSS, 0.9, cross=True, fix_hint="Fix LOKi deeplink IPC before React hydration"),
    _p("H028", "WatchFree deeplink IPC failure", r"deeplink.*IPC|IPC.*deeplink.*watchfree", "high", _L.HTML5, _EC.WATCHFREE_DEEPLINK_LOSS, 0.85, cross=True, fix_hint="Serialise deeplink before React app mount"),
    # CHROMIUM_VERSION_COMPAT
    _p("H029", "Chromium version mismatch", r"chromium.*version.*incompatible|chromium.*compat.*error", "medium", _L.HTML5, _EC.CHROMIUM_VERSION_COMPAT, 0.85, fix_hint="Check Chromium minimum version for API usage"),
    _p("H030", "Deprecated Web API", r"deprecated.*API|API.*deprecated.*chromium", "low", _L.HTML5, _EC.CHROMIUM_VERSION_COMPAT, 0.7, fix_hint="Replace deprecated Web API"),
    # FOCUS_MANAGEMENT
    _p("H031", "Focus lost on navigation", r"focus.*lost.*navigat|navigation.*focus.*lost", "medium", _L.HTML5, _EC.FOCUS_MANAGEMENT, 0.85, fix_hint="Preserve focus on route change"),
    _p("H032", "SpatialNavigation failure", r"SpatialNavigation.*fail|spatial.*nav.*error", "medium", _L.HTML5, _EC.FOCUS_MANAGEMENT, 0.8, fix_hint="Enable SpatialNavigation fallback"),
    # MEMORY_LEAK_EVENT_LISTENER
    _p("H033", "Event listener leak", r"addEventListener.*leak|event.*listener.*not.*removed", "medium", _L.HTML5, _EC.MEMORY_LEAK_EVENT_LISTENER, 0.85, fix_hint="Remove event listeners in component cleanup"),
    _p("H034", "DOM node leak", r"DOM.*node.*leak|leak.*DOM.*node", "medium", _L.HTML5, _EC.MEMORY_LEAK_EVENT_LISTENER, 0.8, fix_hint="Detach DOM nodes before dropping references"),
    _p("H035", "React component leak", r"React.*component.*leak|unmount.*leak", "medium", _L.HTML5, _EC.MEMORY_LEAK_EVENT_LISTENER, 0.75, fix_hint="Check componentWillUnmount for cleanup"),
]

# ─────────────────────────────────────────────────────────────────────────────
# MediaTek Driver (6 categories) — Auto-Escalate, No Fix Generated
# ─────────────────────────────────────────────────────────────────────────────
_MEDIATEK_PATTERNS: list[EnrichedErrorPattern] = [
    # MTK_VDEC_CRASH
    _p("M001", "MTK VDEC crash", r"VDEC.*crash|vdec.*kernel.*oops|mtk.*video.*decoder.*fail", "critical", _L.MEDIATEK, _EC.MTK_VDEC_CRASH, 0.97, fix_hint="Auto-escalate to MediaTek – no fix generated"),
    _p("M002", "VDEC kernel oops", r"Oops.*VDEC|kernel.*oops.*vdec", "critical", _L.MEDIATEK, _EC.MTK_VDEC_CRASH, 0.95, fix_hint="File MediaTek VDEC bug report"),
    _p("M003", "Video decoder hang", r"video.*decoder.*hang|VDEC.*hang", "critical", _L.MEDIATEK, _EC.MTK_VDEC_CRASH, 0.9, fix_hint="Trigger VDEC recovery or reboot"),
    # MTK_MALI_GPU_HANG
    _p("M004", "Mali GPU hang", r"Mali.*GPU.*hang|GPU.*hung.*Mali|mali.*TDR", "critical", _L.MEDIATEK, _EC.MTK_MALI_GPU_HANG, 0.97, fix_hint="Auto-escalate – Mali driver hang"),
    _p("M005", "Mali register dump", r"Mali.*register dump|GPU.*register.*dump", "high", _L.MEDIATEK, _EC.MTK_MALI_GPU_HANG, 0.9, fix_hint="Capture Mali register dump for debugging"),
    _p("M006", "Mali job fault", r"Mali.*job.*fault|JM.*fault.*mali", "high", _L.MEDIATEK, _EC.MTK_MALI_GPU_HANG, 0.88, fix_hint="Check Mali shader compiler output"),
    # MTK_HDCP_FAILURE
    _p("M007", "HDCP authentication failure", r"HDCP.*auth.*fail|HDCP.*2\.2.*error|HDCP.*handshake.*fail", "critical", _L.MEDIATEK, _EC.MTK_HDCP_FAILURE, 0.95, fix_hint="Verify HDCP keys and HDMI sink support"),
    _p("M008", "HDCP re-auth loop", r"HDCP.*re-?auth.*loop|repeated.*HDCP.*fail", "high", _L.MEDIATEK, _EC.MTK_HDCP_FAILURE, 0.85, fix_hint="Check HDMI cable / HDCP repeater"),
    # MTK_TEE_WIDEVINE
    _p("M009", "TEE Widevine error", r"TEE.*Widevine.*error|Widevine.*TEE.*fail", "critical", _L.MEDIATEK, _EC.MTK_TEE_WIDEVINE, 0.97, fix_hint="Auto-escalate – TEE firmware issue"),
    _p("M010", "Widevine L1 CDM error", r"Widevine.*L1.*error|WVCdm.*error", "critical", _L.MEDIATEK, _EC.MTK_TEE_WIDEVINE, 0.95, fix_hint="Reprovision Widevine L1 device"),
    # MTK_ADSP_CRASH
    _p("M011", "ADSP crash", r"ADSP.*crash|audio.*DSP.*crash|mtk.*adsp.*fault", "critical", _L.MEDIATEK, _EC.MTK_ADSP_CRASH, 0.97, fix_hint="Auto-escalate – ADSP firmware crash"),
    _p("M012", "ADSP timeout", r"ADSP.*timeout|audio.*dsp.*timeout", "high", _L.MEDIATEK, _EC.MTK_ADSP_CRASH, 0.85, fix_hint="Check ADSP IPC timeout"),
    # MTK_MMC_IO_ERROR
    _p("M013", "MMC I/O error", r"mmcblk.*I/O error|blk_update_request.*I/O error.*mmcblk", "critical", _L.MEDIATEK, _EC.MTK_MMC_IO_ERROR, 0.97, fix_hint="eMMC storage failure – escalate hardware"),
    _p("M014", "eMMC read failure", r"mmc.*read.*fail|failed.*read.*mmc", "high", _L.MEDIATEK, _EC.MTK_MMC_IO_ERROR, 0.9, fix_hint="Check eMMC health and retry logic"),
    _p("M015", "Flash write timeout", r"flash.*write.*timeout|NAND.*write.*timeout", "critical", _L.MEDIATEK, _EC.MTK_MMC_IO_ERROR, 0.88, fix_hint="Verify flash wear levelling and block health"),
]

# ─────────────────────────────────────────────────────────────────────────────
# Additional high-value patterns
# ─────────────────────────────────────────────────────────────────────────────
_EXTRA_PATTERNS: list[EnrichedErrorPattern] = [
    _p("X001", "LOKi process crash line", r"/3rd/loki/bin/loki.*crash|crashed.*loki", "critical", _L.LOKI, _EC.LOKI_SEGFAULT_NULL_DEREF, 0.88, fix_hint="Inspect LOKi crash tombstone"),
    _p("X002", "Android tombstone begin", r"-----\s*beginning of crash\s*-----", "critical", _L.LOKI, _EC.LOKI_SEGFAULT_NULL_DEREF, 0.7, fix_hint="Read full tombstone for crash details"),
    _p("X003", "Kernel NULL ptr deref", r"Unable to handle kernel NULL pointer dereference", "critical", _L.MEDIATEK, _EC.MTK_VDEC_CRASH, 0.9, fix_hint="Kernel NULL deref – escalate to driver team"),
    _p("X004", "LOKi watchdog timeout", r"watchdog.*timeout.*loki|loki.*watchdog.*expired", "high", _L.LOKI, _EC.LOKI_APP_LAUNCH_FAILURE, 0.8, fix_hint="Increase watchdog timeout or fix blocking call"),
    _p("X005", "LOKi IPC timeout", r"IPC.*timeout.*loki|loki.*IPC.*timeout", "high", _L.LOKI, _EC.LOKI_IR_ROUTING_FAILURE, 0.8, cross=True, fix_hint="Check binder / IPC queue depth"),
    _p("X006", "Chromium renderer crash", r"Renderer.*crash|[Cc]ontent.*[Rr]ender.*crash", "high", _L.HTML5, _EC.JS_HEAP_OOM, 0.75, fix_hint="Check Chromium renderer OOM policy"),
    _p("X007", "LOKi SIGABRT frame 0", r"signal 6 .SIGABRT.*loki|SIGABRT.*#00.*loki", "critical", _L.LOKI, _EC.LOKI_SEGFAULT_NULL_DEREF, 0.92, fix_hint="SIGABRT in LOKi – inspect abort() call site"),
    _p("X008", "WatchFree cross-layer", r"WatchFree.*LOKi.*deeplink|deeplink.*watchfree.*ipc", "high", _L.CROSS_LAYER, _EC.WATCHFREE_DEEPLINK_LOSS, 0.9, cross=True, fix_hint="Coordinate LOKi IPC + React hydration timing"),
    _p("X009", "Companion deadlock cross-layer", r"companion.*deadlock.*chromium|chromium.*companion.*hang", "critical", _L.CROSS_LAYER, _EC.LOKI_COMPANION_SERVER_DEADLOCK, 0.92, cross=True, fix_hint="Fix companion server async protocol"),
    _p("X010", "HDCP + Widevine correlation", r"HDCP.*fail.*widevine|widevine.*fail.*HDCP", "critical", _L.MEDIATEK, _EC.MTK_HDCP_FAILURE, 0.9, fix_hint="Verify HDCP 2.2 + Widevine L1 stack"),
]

_ALL_PATTERNS: list[EnrichedErrorPattern] = (
    _LOKI_PATTERNS + _HTML5_PATTERNS + _MEDIATEK_PATTERNS + _EXTRA_PATTERNS
)


def load_enriched_patterns() -> list[EnrichedErrorPattern]:
    """
    Return all enriched error patterns.

    Returns:
        List of :class:`EnrichedErrorPattern` covering all 27 error categories.
    """
    return list(_ALL_PATTERNS)
