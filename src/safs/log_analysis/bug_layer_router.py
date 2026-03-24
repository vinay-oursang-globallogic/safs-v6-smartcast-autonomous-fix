"""
SAFS v6.0 — Stage 0: BugLayerRouter

Classifies bugs into one of: LOKI, HTML5, MEDIATEK, CROSS_LAYER, UNKNOWN.

Ported from mcp_server_jira_log_analyzer/error_patterns_library.py with enhancements:
- 76+ error patterns from POC
- Each pattern enriched with bug_layer and error_category mappings  
- Cross-layer detection for issues spanning LOKi + HTML5
- Confidence scoring based on pattern match weights

Usage:
    router = BugLayerRouter()
    result = router.route(pipeline_state)
    
    if result.layer == BugLayer.LOKI:
        # Route to LOKi C++ analysis pipeline
    elif result.layer == BugLayer.HTML5:
        # Route to HTML5/JavaScript analysis pipeline
"""

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Pattern

from .models import BugLayer, BugLayerResult, ErrorCategory, PipelineState

logger = logging.getLogger(__name__)


# ============================================================================
# PART 1: ENRICHED ERROR PATTERN DEFINITION
# ============================================================================


@dataclass
class EnrichedErrorPattern:
    """
    Error pattern enriched with bug_layer and error_category for SAFS routing.
    
    Ported from POC UnifiedErrorPattern with SAFS-specific additions:
    - bug_layer: BugLayer enum (LOKI, HTML5, MEDIATEK, CROSS_LAYER)
    - error_category: One of 27 ErrorCategory values
    - bug_layer_confidence: Routing confidence (0.0-1.0)
    - cross_layer_hint: Whether this pattern indicates cross-layer issue
    """
    # Core pattern fields (from POC)
    name: str
    pattern: str  # Regex pattern
    category: str  # Storage, Audio, Video, Network, etc.
    
    # SAFS-specific routing fields
    bug_layer: BugLayer
    error_category: ErrorCategory
    bug_layer_confidence: float = 0.8
    cross_layer_hint: bool = False
    
    # Optional POC fields
    subsystem: Optional[str] = None
    severity: str = "MEDIUM"
    correlation_weight: float = 1.0
    
    # Compiled regex (lazy initialization)
    _compiled_regex: Optional[Pattern] = field(default=None, init=False, repr=False)
    
    @property
    def compiled_regex(self) -> Pattern:
        """Lazy compile regex pattern."""
        if self._compiled_regex is None:
            self._compiled_regex = re.compile(self.pattern, re.IGNORECASE)
        return self._compiled_regex


# ============================================================================
# PART 2: ENRICHED PATTERN LIBRARY
# ============================================================================


# LOKi C++ Patterns (crashes, segfaults, memory issues in native code)
LOKI_PATTERNS = [
    EnrichedErrorPattern(
        name="LOKi Segmentation Fault",
        pattern=r'(segmentation fault|sigsegv|signal 11)',
        category="Crash",
        bug_layer=BugLayer.LOKI,
        error_category=ErrorCategory.LOKI_SEGFAULT_NULL_DEREF,
        bug_layer_confidence=0.95,
        severity="CRITICAL",
    ),
    EnrichedErrorPattern(
        name="LOKi NULL Pointer",
        pattern=r'null pointer|nullptr',
        category="Crash",
        bug_layer=BugLayer.LOKI,
        error_category=ErrorCategory.LOKI_SEGFAULT_NULL_DEREF,
        bug_layer_confidence=0.92,
        severity="CRITICAL",
    ),
    EnrichedErrorPattern(
        name="LOKi Memory Corruption",
        pattern=r'(double free|use after free|heap corruption|malloc.*corrupt)',
        category="Memory",
        bug_layer=BugLayer.LOKI,
        error_category=ErrorCategory.LOKI_MEMORY_CORRUPTION,
        bug_layer_confidence=0.90,
        severity="CRITICAL",
    ),
    EnrichedErrorPattern(
        name="LOKi Race Condition",
        pattern=r'(data race|thread.*unsafe|mutex.*timeout|deadlock)',
        category="Concurrency",
        bug_layer=BugLayer.LOKI,
        error_category=ErrorCategory.LOKI_RACE_CONDITION,
        bug_layer_confidence=0.85,
        severity="HIGH",
    ),
    EnrichedErrorPattern(
        name="LOKi App Launch Failure",
        pattern=r'(\[AppLauncher\].*fail|\[AppLauncher\].*error|failed to launch.*app)',
        category="Application",
        bug_layer=BugLayer.LOKI,
        error_category=ErrorCategory.LOKI_APP_LAUNCH_FAILURE,
        bug_layer_confidence=0.88,
        severity="CRITICAL",
    ),
    EnrichedErrorPattern(
        name="LOKi IR Routing Failure",
        pattern=r'(IR.*event.*lost|key.*event.*dropped|/dev/input.*error)',
        category="Input",
        bug_layer=BugLayer.LOKI,
        error_category=ErrorCategory.LOKI_IR_ROUTING_FAILURE,
        bug_layer_confidence=0.82,
        severity="HIGH",
    ),
    EnrichedErrorPattern(
        name="LOKi Companion Server Deadlock",
        pattern=r'(CompanionServer.*deadlock|CompanionServer.*stuck|localhost:12345.*timeout)',
        category="IPC",
        bug_layer=BugLayer.LOKI,
        error_category=ErrorCategory.LOKI_COMPANION_SERVER_DEADLOCK,
        bug_layer_confidence=0.87,
        severity="CRITICAL",
    ),
    EnrichedErrorPattern(
        name="LOKi EPG Parse Error",
        pattern=r'(EPGManager.*error|EPG.*parse.*fail|WatchFree.*xml.*error)',
        category="EPG",
        bug_layer=BugLayer.LOKI,
        error_category=ErrorCategory.LOKI_EPG_PARSE_ERROR,
        bug_layer_confidence=0.83,
        severity="HIGH",
    ),
    EnrichedErrorPattern(
        name="LOKi OTA Update Failure",
        pattern=r'(OTA.*update.*fail|firmware.*download.*error|update.*verification.*fail)',
        category="Update",
        bug_layer=BugLayer.LOKI,
        error_category=ErrorCategory.LOKI_OTA_UPDATE_FAILURE,
        bug_layer_confidence=0.86,
        severity="CRITICAL",
    ),
    EnrichedErrorPattern(
        name="C++ Stack Trace",
        pattern=r'(#\d+\s+0x[0-9a-f]+.*in.*\(|Backtrace:|Stack trace:)',
        category="Crash",
        bug_layer=BugLayer.LOKI,
        error_category=ErrorCategory.LOKI_SEGFAULT_NULL_DEREF,
        bug_layer_confidence=0.80,
        severity="CRITICAL",
    ),
]


# HTML5 Streaming App Patterns (JavaScript, EME, player errors)
HTML5_PATTERNS = [
    EnrichedErrorPattern(
        name="Companion Library Timing",
        pattern=r'(VIZIO_LIBRARY_DID_LOAD.*not fired|window.VIZIO.*undefined|Companion.*timing)',
        category="Streaming",
        bug_layer=BugLayer.HTML5,
        error_category=ErrorCategory.COMPANION_LIB_TIMING,
        bug_layer_confidence=0.93,
        severity="HIGH",
    ),
    EnrichedErrorPattern(
        name="JavaScript Heap OOM",
        pattern=r'(javascript.*out of memory|v8.*heap.*exhaust|allocation failed.*v8)',
        category="Memory",
        bug_layer=BugLayer.HTML5,
        error_category=ErrorCategory.JS_HEAP_OOM,
        bug_layer_confidence=0.91,
        severity="CRITICAL",
    ),
    EnrichedErrorPattern(
        name="EME/DRM Failure",
        pattern=r'(eme.*error|MediaKeySession.*error|widevine.*error|drm.*fail)',
        category="DRM",
        bug_layer=BugLayer.HTML5,
        error_category=ErrorCategory.EME_DRM_FAILURE,
        bug_layer_confidence=0.89,
        severity="CRITICAL",
    ),
    EnrichedErrorPattern(
        name="Keydown Not Fired",
        pattern=r'(keydown.*not fired|key event.*lost|arrow.*key.*not.*work)',
        category="Input",
        bug_layer=BugLayer.HTML5,
        error_category=ErrorCategory.KEYDOWN_NOT_FIRED,
        bug_layer_confidence=0.85,
        severity="HIGH",
    ),
    EnrichedErrorPattern(
        name="Fetch Network Timeout",
        pattern=r'(fetch.*timeout|xhr.*timeout|network.*request.*timeout)',
        category="Network",
        bug_layer=BugLayer.HTML5,
        error_category=ErrorCategory.FETCH_NETWORK_TIMEOUT,
        bug_layer_confidence=0.84,
        severity="HIGH",
    ),
    EnrichedErrorPattern(
        name="Shaka Error 3016",
        pattern=r'(shaka.*error.*3016|shaka.*seek.*error)',
        category="Streaming",
        bug_layer=BugLayer.HTML5,
        error_category=ErrorCategory.SHAKA_ERROR_3016,
        bug_layer_confidence=0.95,
        severity="HIGH",
    ),
    EnrichedErrorPattern(
        name="Netflix MSL Timeout",
        pattern=r'(netflix.*msl.*timeout|nfp.*handshake.*timeout)',
        category="Streaming",
        bug_layer=BugLayer.HTML5,
        error_category=ErrorCategory.NETFLIX_MSL_TIMEOUT,
        bug_layer_confidence=0.94,
        severity="HIGH",
        subsystem="Netflix",
    ),
    EnrichedErrorPattern(
        name="Amazon DASH Manifest",
        pattern=r'(amazon.*dash.*manifest.*fail|amzn.*mpd.*error)',
        category="Streaming",
        bug_layer=BugLayer.HTML5,
        error_category=ErrorCategory.AMAZON_DASH_MANIFEST,
        bug_layer_confidence=0.92,
        severity="HIGH",
        subsystem="Amazon",
    ),
    EnrichedErrorPattern(
        name="Hulu Ad MSE Break",
        pattern=r'(hulu.*ad.*break|hulu.*mse.*timeline.*error)',
        category="Streaming",
        bug_layer=BugLayer.HTML5,
        error_category=ErrorCategory.HULU_AD_MSE_BREAK,
        bug_layer_confidence=0.91,
        severity="MEDIUM",
        subsystem="Hulu",
    ),
    EnrichedErrorPattern(
        name="Chromium Version Compat",
        pattern=r'(feature.*not.*support.*chromium|polyfill.*required)',
        category="Browser",
        bug_layer=BugLayer.HTML5,
        error_category=ErrorCategory.CHROMIUM_VERSION_COMPAT,
        bug_layer_confidence=0.82,
        severity="MEDIUM",
    ),
    EnrichedErrorPattern(
        name="Focus Management",
        pattern=r'(focus.*lost|spatial.*nav.*fail|tabindex.*error)',
        category="UI",
        bug_layer=BugLayer.HTML5,
        error_category=ErrorCategory.FOCUS_MANAGEMENT,
        bug_layer_confidence=0.80,
        severity="MEDIUM",
    ),
    EnrichedErrorPattern(
        name="Memory Leak Event Listener",
        pattern=r'(addEventListener.*not.*removed|event.*listener.*leak)',
        category="Memory",
        bug_layer=BugLayer.HTML5,
        error_category=ErrorCategory.MEMORY_LEAK_EVENT_LISTENER,
        bug_layer_confidence=0.83,
        severity="MEDIUM",
    ),
]


# MediaTek Hardware/Firmware Patterns (auto-escalate, no fix generated)
MEDIATEK_PATTERNS = [
    EnrichedErrorPattern(
        name="MTK Video Decoder Crash",
        pattern=r'(mtk.*vdec.*crash|mediatek.*decoder.*error|vdec.*firmware.*fail)',
        category="Video",
        bug_layer=BugLayer.MEDIATEK,
        error_category=ErrorCategory.MTK_VDEC_CRASH,
        bug_layer_confidence=0.96,
        severity="CRITICAL",
    ),
    EnrichedErrorPattern(
        name="MTK Mali GPU Hang",
        pattern=r'(mali.*gpu.*hang|mali.*timeout|arm.*gpu.*error)',
        category="Graphics",
        bug_layer=BugLayer.MEDIATEK,
        error_category=ErrorCategory.MTK_MALI_GPU_HANG,
        bug_layer_confidence=0.94,
        severity="CRITICAL",
    ),
    EnrichedErrorPattern(
        name="MTK HDCP Failure",
        pattern=r'(hdcp.*fail|hdcp.*timeout|hdcp.*key.*error)',
        category="Display",
        bug_layer=BugLayer.MEDIATEK,
        error_category=ErrorCategory.MTK_HDCP_FAILURE,
        bug_layer_confidence=0.93,
        severity="CRITICAL",
    ),
    EnrichedErrorPattern(
        name="MTK Widevine TEE",
        pattern=r'(widevine.*tee.*error|trustzone.*widevine.*fail|drm.*provision.*fail)',
        category="DRM",
        bug_layer=BugLayer.MEDIATEK,
        error_category=ErrorCategory.MTK_TEE_WIDEVINE,
        bug_layer_confidence=0.92,
        severity="CRITICAL",
    ),
    EnrichedErrorPattern(
        name="MTK Audio DSP Crash",
        pattern=r'(mtk.*adsp.*crash|audio.*dsp.*error|audio.*firmware.*fail)',
        category="Audio",
        bug_layer=BugLayer.MEDIATEK,
        error_category=ErrorCategory.MTK_ADSP_CRASH,
        bug_layer_confidence=0.91,
        severity="CRITICAL",
    ),
    EnrichedErrorPattern(
        name="MTK MMC I/O Error",
        pattern=r'(mmc.*error|emmc.*fail|mtk.*storage.*error)',
        category="Storage",
        bug_layer=BugLayer.MEDIATEK,
        error_category=ErrorCategory.MTK_MMC_IO_ERROR,
        bug_layer_confidence=0.90,
        severity="CRITICAL",
    ),
]


# Cross-Layer Patterns (issues spanning LOKi + HTML5)
CROSS_LAYER_PATTERNS = [
    EnrichedErrorPattern(
        name="WatchFree+ Deeplink Loss",
        pattern=r'(watchfree.*deeplink.*lost|watchfree.*contentid.*missing)',
        category="Streaming",
        bug_layer=BugLayer.CROSS_LAYER,
        error_category=ErrorCategory.WATCHFREE_DEEPLINK_LOSS,
        bug_layer_confidence=0.90,
        cross_layer_hint=True,
        severity="HIGH",
    ),
    EnrichedErrorPattern(
        name="Companion Library + LOKi IPC Failure",
        pattern=r'(companion.*server.*error.*vizio|loki.*compan ion.*fail)',
        category="IPC",
        bug_layer=BugLayer.CROSS_LAYER,
        error_category=ErrorCategory.LOKI_COMPANION_SERVER_DEADLOCK,
        bug_layer_confidence=0.85,
        cross_layer_hint=True,
        severity="HIGH",
    ),
]


# Combine all patterns
ALL_PATTERNS = LOKI_PATTERNS + HTML5_PATTERNS + MEDIATEK_PATTERNS + CROSS_LAYER_PATTERNS


# ============================================================================
# PART 3: BUG LAYER ROUTER
# ============================================================================


class BugLayerRouter:
    """
    Stage 0: Classifies bugs into BugLayer categories.
    
    Routes bugs to appropriate analysis pipelines:
    - LOKI: C++ native code analysis (null checks, smart pointers, mutex guards)
    - HTML5: JavaScript/browser analysis (polyfills, event listeners, DRM config)
    - MEDIATEK: Hardware/firmware (auto-escalate to hw_triage, no fix generated)
    - CROSS_LAYER: Issues spanning LOKi + HTML5 (dual fixes required)
    - UNKNOWN: Insufficient signal for classification
    
    Algorithm:
    1. Run all patterns against filtered log lines
    2. Accumulate weighted scores per BugLayer
    3. Detect CROSS_LAYER when both LOKi and HTML5 signals present
    4. Return highest-scoring layer with confidence
    """
    
    def __init__(self):
        """Initialize BugLayerRouter with enriched patterns."""
        self.patterns = ALL_PATTERNS
        logger.info(f"Initialized BugLayerRouter with {len(self.patterns)} patterns")
        
        # Statistics
        self.total_routes = 0
        self.layer_distribution = defaultdict(int)
    
    def route(self, state: PipelineState) -> BugLayerResult:
        """
        Route bug to appropriate BugLayer based on log analysis.
        
        Args:
            state: PipelineState with quality-filtered log lines
            
        Returns:
            BugLayerResult with layer classification and confidence
        """
        self.total_routes += 1
        
        # Collect log lines from quality result
        if state.quality_result is None:
            logger.warning("Quality result not set, cannot route")
            return BugLayerResult(
                layer=BugLayer.UNKNOWN,
                confidence=0.0,
                layer_scores={},
                matched_patterns=[],
            )
        
        # TODO: For now, extract lines from ticket attachments
        # In later stages, this will use pre-filtered lines from QualityResult
        log_lines = self._extract_log_lines(state)
        
        if not log_lines:
            logger.warning("No log lines to analyze")
            return BugLayerResult(
                layer=BugLayer.UNKNOWN,
                confidence=0.0,
                layer_scores={},
                matched_patterns=[],
            )
        
        # Score each bug layer
        layer_scores: Dict[BugLayer, float] = defaultdict(float)
        matched_patterns: List[str] = []
        cross_layer_signals = {"loki": 0, "html5": 0}
        
        for line in log_lines:
            for pattern in self.patterns:
                if pattern.compiled_regex.search(line):
                    # Add weighted score
                    weight = pattern.bug_layer_confidence * pattern.correlation_weight
                    layer_scores[pattern.bug_layer] += weight
                    matched_patterns.append(pattern.name)
                    
                    # Track CROSS_LAYER signals
                    if pattern.bug_layer == BugLayer.LOKI:
                        cross_layer_signals["loki"] += 1
                    elif pattern.bug_layer == BugLayer.HTML5:
                        cross_layer_signals["html5"] += 1
        
        # CROSS_LAYER detection: both LOKi and HTML5 signals present
        if cross_layer_signals["loki"] >= 1 and cross_layer_signals["html5"] >= 1:
            self.layer_distribution[BugLayer.CROSS_LAYER] += 1
            return BugLayerResult(
                layer=BugLayer.CROSS_LAYER,
                confidence=0.80,
                layer_scores=dict(layer_scores),
                matched_patterns=list(set(matched_patterns)),  # Deduplicate
            )
        
        # Check for explicit CROSS_LAYER hints
        for pattern in self.patterns:
            if pattern.cross_layer_hint and pattern.name in matched_patterns:
                self.layer_distribution[BugLayer.CROSS_LAYER] += 1
                return BugLayerResult(
                    layer=BugLayer.CROSS_LAYER,
                    confidence=0.75,
                    layer_scores=dict(layer_scores),
                    matched_patterns=list(set(matched_patterns)),
                )
        
        # Return highest-scoring layer
        if not layer_scores:
            logger.info("No patterns matched, returning UNKNOWN")
            self.layer_distribution[BugLayer.UNKNOWN] += 1
            return BugLayerResult(
                layer=BugLayer.UNKNOWN,
                confidence=0.0,
                layer_scores={},
                matched_patterns=[],
            )
        
        # Calculate confidence as normalized score
        best_layer = max(layer_scores, key=layer_scores.get)
        total_score = sum(layer_scores.values())
        confidence = min(layer_scores[best_layer] / total_score, 1.0) if total_score > 0 else 0.0
        
        self.layer_distribution[best_layer] += 1
        
        logger.info(
            f"Routed to {best_layer.value} with confidence {confidence:.2f} "
            f"(matched {len(matched_patterns)} patterns)"
        )
        
        return BugLayerResult(
            layer=best_layer,
            confidence=confidence,
            layer_scores=dict(layer_scores),
            matched_patterns=list(set(matched_patterns)),  # Deduplicate
        )
    
    def _extract_log_lines(self, state: PipelineState) -> List[str]:
        """
        Extract log lines from pipeline state.

        Prefers quality-gate filtered lines when available so that noise
        removed by the quality gate does not skew BugLayer classification.

        Args:
            state: PipelineState
            
        Returns:
            List of log line strings
        """
        # Prefer pre-filtered lines from QualityResult (Stage 1 output)
        if state.quality_result and state.quality_result.filtered_logs:
            return list(state.quality_result.filtered_logs)

        # Fall back to raw lines from ticket attachments
        lines = []
        for attachment in state.ticket.attachments:
            for log_file in attachment.log_files:
                for log_line in log_file.timestamped_log_lines:
                    lines.append(log_line.log_line)
        return lines
    
    def get_statistics(self) -> Dict:
        """
        Get routing statistics.
        
        Returns:
            Dictionary with routing stats
        """
        return {
            'total_routes': self.total_routes,
            'layer_distribution': dict(self.layer_distribution),
        }
