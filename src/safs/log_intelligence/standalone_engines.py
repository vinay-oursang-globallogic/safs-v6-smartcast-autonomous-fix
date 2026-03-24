"""
SAFS v6.0 — Standalone Log Analysis Engines

Self-contained implementations of:
- SimplifiedDrainParser: DRAIN log template clustering
- EnhancedTimestampExtractor: Multi-format timestamp extraction
- SmartTVErrorAnalyzer: Temporal correlations, incidents, anomalies, cascading failures
- ContextAnalyzer: Jira description → technical keywords

These replace the external POC dependency from mcp_server_jira_log_analyzer,
making SAFS fully self-contained.
"""

from __future__ import annotations

import hashlib
import re
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


# ==================================================================================
# DRAIN PARSER — Log Template Clustering
# ==================================================================================


@dataclass
class LogTemplate:
    """POC-compatible log template data class"""

    id: str
    template: str
    count: int
    examples: list[str]
    first_seen: Optional[float] = None
    last_seen: Optional[float] = None


class SimplifiedDrainParser:
    """
    DRAIN (Deep neural network log parsing) simplified implementation.

    Clusters log lines into templates by:
    1. Grouping by token count
    2. Matching against existing templates using similarity
    3. Generalising differing tokens to <*> wildcards

    Reference: He et al. "Drain: An Online Log Parsing Approach with Fixed Depth Tree"
    """

    # Tokens that are always wildcards (numbers, IPs, hex, paths, UUIDs)
    _VAR_RE = re.compile(
        r"(?<!\S)("
        r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}"  # IPv4
        r"|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"  # UUID
        r"|0x[0-9a-fA-F]+"  # hex
        r"|/[\w./\-]+"  # file paths
        r"|\d+"  # numbers
        r")(?!\S)"
    )

    def __init__(
        self,
        similarity_threshold: float = 0.5,
        max_examples: int = 3,
    ):
        self.similarity_threshold = similarity_threshold
        self.max_examples = max_examples
        self.total_logs = 0
        # key = token_count → list of (template_tokens, template_obj)
        self._tree: dict[int, list[tuple[list[str], LogTemplate]]] = defaultdict(list)

    def add_log(self, line: str) -> None:
        """Add a log line to the parser and cluster it."""
        self.total_logs += 1
        now_ts = time.time()
        tokens = self._tokenize(line)
        n = len(tokens)

        best_match = None
        best_score = -1.0

        for tmpl_tokens, tmpl_obj in self._tree[n]:
            score = self._similarity(tokens, tmpl_tokens)
            if score > best_score:
                best_score = score
                best_match = (tmpl_tokens, tmpl_obj)

        if best_match is not None and best_score >= self.similarity_threshold:
            tmpl_tokens, tmpl_obj = best_match
            # Update template: tokens that differ become <*>
            merged = [
                t if t == tmpl_tokens[i] else "<*>"
                for i, t in enumerate(tokens)
            ]
            tmpl_obj.count += 1
            if len(tmpl_obj.examples) < self.max_examples:
                tmpl_obj.examples.append(line[: 200])
            if tmpl_obj.last_seen is None or tmpl_obj.last_seen < now_ts:
                tmpl_obj.last_seen = now_ts
            # Update stored tokens in-place
            tmpl_tokens[:] = merged
            tmpl_obj.template = " ".join(merged)
        else:
            # New template
            tmpl_str = " ".join(tokens)
            tmpl_id = hashlib.md5(tmpl_str.encode(), usedforsecurity=False).hexdigest()[:16]
            new_tmpl = LogTemplate(
                id=tmpl_id,
                template=tmpl_str,
                count=1,
                examples=[line[:200]],
                first_seen=now_ts,
                last_seen=now_ts,
            )
            self._tree[n].append((list(tokens), new_tmpl))

    def get_templates(self) -> list[LogTemplate]:
        """Return all discovered templates sorted by count descending."""
        result = []
        for entries in self._tree.values():
            for _, tmpl in entries:
                result.append(tmpl)
        result.sort(key=lambda t: t.count, reverse=True)
        return result

    def get_reduction_ratio(self) -> float:
        """Calculate deduplication ratio (templates / total logs)."""
        if self.total_logs == 0:
            return 0.0
        n_templates = sum(len(v) for v in self._tree.values())
        return 1.0 - (n_templates / self.total_logs)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _tokenize(self, line: str) -> list[str]:
        """Tokenize log line, replacing variable parts with <*>."""
        normalized = self._VAR_RE.sub("<*>", line)
        return normalized.strip().split()

    @staticmethod
    def _similarity(a: list[str], b: list[str]) -> float:
        """Compute sequence similarity between two token lists."""
        if len(a) != len(b):
            return 0.0
        if not a:
            return 1.0
        matches = sum(1 for x, y in zip(a, b) if x == y or x == "<*>" or y == "<*>")
        return matches / len(a)


# ==================================================================================
# TIMESTAMP EXTRACTOR
# ==================================================================================


class EnhancedTimestampExtractor:
    """
    Multi-format timestamp extractor for SmartTV log files.

    Supports:
    - Kernel uptime:  [  417.695436]
    - ISO8601:        2025-12-11T14:30:45.123Z
    - Syslog:         Dec 10 16:57:32.011815
    - Android logcat: 12-15 14:30:45.123
    """

    _KERNEL_RE = re.compile(r"^\[\s*(\d+\.\d+)\]")
    _ISO8601_RE = re.compile(
        r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?)"
    )
    _SYSLOG_RE = re.compile(
        r"^(\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?)"
    )
    _LOGCAT_RE = re.compile(
        r"^(\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d+)"
    )

    def extract_timestamp_from_line(
        self, line: str, log_path: Optional[str] = None
    ) -> Optional[datetime]:
        """Extract timestamp from a log line, returning UTC datetime or None."""
        # Try ISO8601 first (most precise)
        m = self._ISO8601_RE.search(line)
        if m:
            try:
                ts_str = m.group(1)
                if ts_str.endswith("Z"):
                    ts_str = ts_str[:-1] + "+00:00"
                return datetime.fromisoformat(ts_str).astimezone(timezone.utc)
            except ValueError:
                pass

        # Android logcat  MM-DD HH:MM:SS.mmm
        m = self._LOGCAT_RE.match(line.strip())
        if m:
            try:
                year = datetime.now(timezone.utc).year
                ts_str = f"{year}-{m.group(1).replace(' ', 'T')}"
                return datetime.fromisoformat(ts_str).replace(tzinfo=timezone.utc)
            except ValueError:
                pass

        # Syslog  "Dec 10 16:57:32"
        m = self._SYSLOG_RE.match(line.strip())
        if m:
            try:
                year = datetime.now(timezone.utc).year
                return datetime.strptime(
                    f"{year} {m.group(1)}", "%Y %b %d %H:%M:%S"
                ).replace(tzinfo=timezone.utc)
            except ValueError:
                pass

        # Kernel uptime — relative, cannot convert to wall-clock without boot time
        m = self._KERNEL_RE.match(line.strip())
        if m:
            # Return epoch + uptime seconds as a synthetic timestamp
            uptime = float(m.group(1))
            return datetime.fromtimestamp(uptime, tz=timezone.utc)

        return None


# ==================================================================================
# SMART TV ERROR ANALYZER POC DATA CLASSES
# ==================================================================================


@dataclass
class ErrorCorrelation:
    """POC-compatible error correlation"""
    error1: str
    error2: str
    count: int
    avg_time_diff: float = 0.0
    confidence: float = 0.0


@dataclass
class Incident:
    """POC-compatible incident"""
    incident_id: str
    start_time: float
    end_time: float
    duration: float
    error_count: int
    unique_error_types: set[str]
    root_cause_candidates: list[str]
    severity: str


@dataclass
class Anomaly:
    """POC-compatible anomaly"""
    error_type: str
    window_start: float
    window_end: float
    baseline_rate: float
    spike_rate: float
    spike_magnitude: float


# ==================================================================================
# SMART TV ERROR ANALYZER — 5 Analysis Engines
# ==================================================================================


class SmartTVErrorAnalyzer:
    """
    SmartTV error analysis with 5 engines:
    1. Temporal correlations (errors that follow each other)
    2. Incident detection (gap-based clustering of error bursts)
    3. Anomaly detection (3× baseline spike detection)
    4. Cascading failure chains
    5. Heuristic root cause inference

    Standalone replacement for the external POC SmartTVErrorAnalyzer.
    """

    # Error-type extraction patterns for each known error class
    _ERROR_PATTERNS: dict[str, re.Pattern[str]] = {
        "COMPANION_TIMEOUT": re.compile(r"companion.*timeout|timeout.*companion", re.I),
        "APP_LAUNCH_FAIL": re.compile(r"launch.*fail|failed.*launch|app.*crash", re.I),
        "NULL_DEREF": re.compile(r"null.*deref|sigsegv|null pointer|segfault", re.I),
        "OOM": re.compile(r"out.of.memory|oom|malloc.*fail|heap.*exhausted", re.I),
        "DRM_FAIL": re.compile(r"drm.*fail|widevine.*error|eme.*error|hdcp", re.I),
        "NETWORK_TIMEOUT": re.compile(r"connection.*timeout|network.*error|fetch.*fail", re.I),
        "KERNEL_PANIC": re.compile(r"kernel panic|oops:|bug:", re.I),
        "WATCHDOG": re.compile(r"watchdog|soft.lockup|hard.lockup", re.I),
        "AUDIO_FAIL": re.compile(r"audio.*error|adsp.*crash|no.*sound|audio.*fail", re.I),
        "VIDEO_FAIL": re.compile(r"vdec.*crash|video.*error|display.*fail|black.*screen", re.I),
    }

    # Incident gap threshold (seconds): a new incident starts if gap > this
    _INCIDENT_GAP_SECONDS = 60.0

    # Anomaly detection: spike if rate > baseline × SPIKE_FACTOR
    _SPIKE_FACTOR = 3.0

    def __init__(
        self,
        ticket_description: str = "",
        enable_advanced_algorithms: bool = True,
    ):
        self.ticket_description = ticket_description
        self.enable_advanced = enable_advanced_algorithms

    # ------------------------------------------------------------------
    # Engine 1: Temporal Correlations
    # ------------------------------------------------------------------

    def _analyze_correlations(self, log_lines: list[str]) -> list[ErrorCorrelation]:
        """Find error pairs that consistently follow each other."""
        # Classify each line to an error type
        classified: list[Optional[str]] = [self._classify_line(l) for l in log_lines]

        # Sliding window to find A→B patterns within 10 lines
        pair_counts: dict[tuple[str, str], int] = defaultdict(int)
        for i, et in enumerate(classified):
            if et is None:
                continue
            window = classified[i + 1: i + 11]
            for subsequent in window:
                if subsequent and subsequent != et:
                    pair_counts[(et, subsequent)] += 1

        correlations = []
        for (e1, e2), count in pair_counts.items():
            if count >= 2:
                confidence = min(count / 10.0, 1.0)
                correlations.append(
                    ErrorCorrelation(
                        error1=e1,
                        error2=e2,
                        count=count,
                        avg_time_diff=0.0,
                        confidence=confidence,
                    )
                )
        return sorted(correlations, key=lambda c: c.count, reverse=True)

    # ------------------------------------------------------------------
    # Engine 2: Incident Detection
    # ------------------------------------------------------------------

    def _detect_incidents(self, logs: list[Any]) -> list[Incident]:
        """Cluster error-dense windows into incidents using 60s gap threshold."""
        # Extract (timestamp, error_type) pairs
        events: list[tuple[float, str]] = []
        for log in logs:
            ts = getattr(log, "timestamp", None)
            if ts is None:
                continue
            et = self._classify_line(getattr(log, "line", ""))
            if et:
                events.append((float(ts), et))

        if not events:
            return []

        events.sort(key=lambda e: e[0])

        incidents: list[Incident] = []
        cluster_start = events[0][0]
        cluster_end = events[0][0]
        cluster_errors: list[str] = [events[0][1]]

        for ts, et in events[1:]:
            if ts - cluster_end <= self._INCIDENT_GAP_SECONDS:
                cluster_end = ts
                cluster_errors.append(et)
            else:
                # Emit incident
                incidents.append(self._make_incident(cluster_start, cluster_end, cluster_errors))
                cluster_start = ts
                cluster_end = ts
                cluster_errors = [et]

        # Last cluster
        incidents.append(self._make_incident(cluster_start, cluster_end, cluster_errors))
        return incidents

    # ------------------------------------------------------------------
    # Engine 3: Anomaly Detection
    # ------------------------------------------------------------------

    def _detect_anomalies(self, logs: list[Any]) -> list[Anomaly]:
        """Detect 3× spike in error rate over rolling baseline."""
        events: list[tuple[float, str]] = []
        for log in logs:
            ts = getattr(log, "timestamp", None)
            if ts is None:
                continue
            et = self._classify_line(getattr(log, "line", ""))
            if et:
                events.append((float(ts), et))

        if len(events) < 10:
            return []

        events.sort(key=lambda e: e[0])
        total_duration = events[-1][0] - events[0][0] or 1.0

        # Compute baseline rate per error type
        type_events: dict[str, list[float]] = defaultdict(list)
        for ts, et in events:
            type_events[et].append(ts)

        anomalies: list[Anomaly] = []
        for et, tss in type_events.items():
            baseline_rate = len(tss) / total_duration

            # Check if there's a 30s window with spike
            for i, ts in enumerate(tss):
                window_end = ts + 30.0
                window_events = [t for t in tss if ts <= t <= window_end]
                if len(window_events) < 2:
                    continue
                window_rate = len(window_events) / 30.0
                if window_rate > baseline_rate * self._SPIKE_FACTOR:
                    anomalies.append(
                        Anomaly(
                            error_type=et,
                            window_start=ts,
                            window_end=window_end,
                            baseline_rate=baseline_rate,
                            spike_rate=window_rate,
                            spike_magnitude=window_rate / max(baseline_rate, 1e-9),
                        )
                    )
                    break  # One anomaly per error type

        return anomalies

    # ------------------------------------------------------------------
    # Engine 4: Cascading Failure Detection
    # ------------------------------------------------------------------

    def _detect_cascading_failures(self, logs: list[Any]) -> list[Any]:
        """Identify cascading failure chains: A causes B causes C."""
        correlations = self._analyze_correlations(
            [getattr(l, "line", "") for l in logs]
        )

        # Build adjacency list of high-confidence correlations
        adj: dict[str, list[str]] = defaultdict(list)
        for corr in correlations:
            if corr.confidence >= 0.5:
                adj[corr.error1].append(corr.error2)

        # Find chains of length >= 2
        chains: list[list[str]] = []
        visited: set[str] = set()

        def dfs(node: str, path: list[str]) -> None:
            path.append(node)
            if node in visited:
                if len(path) >= 2:
                    chains.append(list(path))
                return
            visited.add(node)
            for nxt in adj.get(node, []):
                dfs(nxt, path)
            if not adj.get(node) and len(path) >= 2:
                chains.append(list(path))
            visited.discard(node)

        for root in list(adj.keys()):
            dfs(root, [])

        # Deduplicate chains longer than 2
        unique_chains: list[list[str]] = []
        seen_chains: set[tuple[str, ...]] = set()
        for ch in chains:
            key = tuple(ch)
            if key not in seen_chains:
                seen_chains.add(key)
                unique_chains.append(ch)

        # Return as simple objects (duck-typed to match POC)
        class CascadeObj:
            def __init__(self, chain: list[str]) -> None:
                self.chain = chain
                self.start_time = 0.0
                self.end_time = 0.0
                self.impact = "MEDIUM"

        return [CascadeObj(ch) for ch in unique_chains[:5]]

    # ------------------------------------------------------------------
    # Engine 5: Heuristic Root Cause Inference
    # ------------------------------------------------------------------

    def _infer_root_causes(
        self,
        correlations: list[Any],
        incidents: list[Any],
        cascading_failures: list[Any],
    ) -> list[str]:
        """Infer likely root causes from analysis results."""
        candidates: list[str] = []

        # High-count correlations suggest causal chain
        for corr in correlations[:3]:
            if hasattr(corr, "confidence") and corr.confidence >= 0.7:
                if hasattr(corr, "error1"):
                    candidates.append(f"Primary error: {corr.error1} causes {corr.error2}")

        # Long incidents suggest systemic issue
        for incident in incidents:
            if hasattr(incident, "duration") and incident.duration > 30:
                ets = list(getattr(incident, "unique_error_types", []))
                if ets:
                    candidates.append(f"Sustained incident ({incident.duration:.0f}s): {ets[0]}")

        # Cascading failures
        for cf in cascading_failures:
            chain = getattr(cf, "chain", [])
            if len(chain) >= 2:
                candidates.append(f"Cascading failure: {' → '.join(chain)}")

        return candidates[:5]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _classify_line(self, line: str) -> Optional[str]:
        """Classify a log line to an error type, or None."""
        for error_type, pattern in self._ERROR_PATTERNS.items():
            if pattern.search(line):
                return error_type
        return None

    @staticmethod
    def _make_incident(
        start: float, end: float, errors: list[str]
    ) -> Incident:
        inc_id = hashlib.md5(f"{start}{end}".encode(), usedforsecurity=False).hexdigest()[:12]
        return Incident(
            incident_id=inc_id,
            start_time=start,
            end_time=end,
            duration=end - start,
            error_count=len(errors),
            unique_error_types=set(errors),
            root_cause_candidates=[],
            severity="HIGH" if len(errors) > 10 else "MEDIUM",
        )


# ==================================================================================
# CONTEXT ANALYZER — Keyword Extraction
# ==================================================================================


class ContextAnalyzer:
    """
    Extracts technical keywords from Jira ticket descriptions.

    Maps colloquial user terms to precise technical search terms used in
    log patterns and code analysis.
    """

    CONTEXT_KEYWORDS: dict[str, list[str]] = {
        # System stability
        "freeze": ["deadlock", "hang", "timeout", "livelock"],
        "crash": ["segfault", "sigsegv", "null_deref", "abort"],
        "hang": ["deadlock", "mutex", "lock", "timeout"],
        "stuck": ["deadlock", "hang", "blocking", "mutex"],
        "unresponsive": ["hang", "deadlock", "cpu_busy", "thread_block"],
        # Display
        "black screen": ["framebuffer", "display_fail", "hdmi", "vdec"],
        "no picture": ["video_fail", "vdec", "framebuffer", "display"],
        "flickering": ["vsync", "framebuffer", "gpu_hang", "tearing"],
        "blurry": ["codec_fail", "bitrate", "resolution", "decode_error"],
        # Audio
        "no sound": ["audio_fail", "adsp_crash", "codec_init", "dsp_error"],
        "audio": ["adsp", "audio_codec", "pcm", "dsp_fail"],
        "mute": ["audio_mute", "adsp_fail", "volume_ctrl"],
        # Network
        "buffering": ["network_timeout", "bandwidth", "cdn_fail", "buffer_underflow"],
        "not loading": ["network_timeout", "fetch_fail", "dns_fail", "connection_error"],
        "slow": ["latency", "timeout", "high_delay", "packet_loss"],
        # Apps
        "netflix": ["netflix_msl", "nrd", "dash_manifest", "cdn_fail"],
        "hulu": ["hulu_drm", "ad_mse", "hls_parse", "ad_insertion"],
        "amazon": ["amazon_dash", "prime_video", "drm_fail", "manifest"],
        "youtube": ["youtube_js", "yt_drm", "adaptive_streaming"],
        "watchfree": ["watchfree_deeplink", "hls_fail", "tuner"],
        # Boot/Power
        "reboot": ["reboot", "restart", "watchdog", "kernel_panic"],
        "not turning on": ["boot_fail", "power_ctrl", "init_fail"],
        "slow boot": ["boot_time", "init_slow", "startup_fail"],
        # Remote/Input
        "remote": ["ir_routing", "keydown", "hid_fail", "input_event"],
        "button": ["keydown", "ir_event", "input_fail"],
        # DRM
        "drm": ["widevine", "hdcp", "eme_fail", "drm_error", "license_fail"],
        "protected": ["hdcp", "widevine_l1", "security_fail"],
        # Memory
        "memory": ["oom", "malloc_fail", "heap_corrupt", "mmap_fail"],
        "out of memory": ["oom", "malloc_fail", "heap_exhausted"],
    }
