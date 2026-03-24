"""
SAFS v6.0 — DRAIN3-based Log Template Clustering (VizioSpecificDrainAdapter)

Provides log template deduplication using the DRAIN3 algorithm, augmented with
Vizio SmartCast-specific masking rules.  When ``drain3`` is not installed the
adapter degrades to a simple exact-match grouper so the rest of the pipeline
can still run.

Masking rules applied (in order):
1.  ANSI / VT100 escape codes stripped (kernel colour output)
2.  UUID strings                    ``xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx``
3.  IPv4 addresses                  ``192.168.1.1``
4.  Hex with 0x prefix              ``0x[A-Fa-f0-9]+``
5.  proctitle bare-hex payload      ``proctitle=646863...``
6.  Bare hex after ``=`` / ``:``    ``arch=40000028``, ``a1=96ebd880``
7.  Hex dump rows                   ``00 01 44 2a f8 3b …`` (8+ bytes)
8.  Journald header prefix          ``Feb 04 18:01:35.123456 LinuxTV`` → ``<JDT>``
9.  Kernel dmesg uptime prefix      ``[  435.831108]`` → ``[<UPTIME>]``
10. journald process-bracket PID    ``conjure.sh[4728]:`` → ``conjure.sh[<PID>]:``
11. Chromium / Cobalt trace header  ``[PID:TID:LEVEL:file.cc(N)]``
12. MTK bracketed source-line nos   ``[2006]``, ``[8259]`` → ``[<NUM>]``
13. ARM backtrace PC / LR / SP
14. LOKi shared-library paths
15. Port numbers with prefix
16. Semantic version strings        ``1.3.10-rc2``
17. Epoch timestamps                ``1770227734``
18. PID / UID / GID keyword forms   ``pid=9156``, ``uid=0``, ``auid=4294967295``
19. Long standalone decimal ints    4+ digit bare numbers → ``<NUM>``
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ─── Masking rules ─────────────────────────────────────────────────────────────
#
# Each rule is a (replacement_token, compiled_regex) pair.  Rules are applied
# in order so tokens that are already replaced cannot be double-processed.
# An empty replacement string ("") means the match is simply deleted.
#
_MASK_RULES: list[tuple[str, re.Pattern]] = [
    # 1. Strip ANSI / VT100 escape codes present in MTK/LOKi kernel output.
    #    Log files may store them as the actual ESC byte (0x1b) OR as the
    #    printable 4-character sequence backslash-x-1-b.  Both forms are
    #    handled: r"\x1b" in a regex pattern matches the ESC byte; r"\\x1b"
    #    matches the literal text backslash+x+1+b.
    #    e.g.  \x1b[1;35m[HDCP1X]...\x1b[m   (MTK dmesg colour output)
    ("", re.compile(r"(?:\x1b|\\x1b)\[[0-9;]*[A-Za-z]")),

    # 2. UUID strings  (before hex/IP to avoid partial collision)
    #    e.g.  0e09c8f7-fd23-4c52-a853-c37117b9dc46
    ("<UUID>", re.compile(
        r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
        r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
    )),

    # 3. IPv4 addresses   192.168.1.100
    ("<IP>", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),

    # 4. Hex addresses / values with 0x prefix   0xdeadbeef  0x4
    ("<HEX>", re.compile(r"\b0x[0-9a-fA-F]+\b")),

    # 5. proctitle bare hex payload (audit log process-name hex encoding)
    #    e.g.  proctitle=646863706364002D2D64756D706C65617365
    ("<HEX>", re.compile(r"(?<=proctitle=)[0-9a-fA-F]{12,}")),

    # 6. Bare hex values after = keyword  (ARM arch/personality codes, register args)
    #    e.g.  arch=40000028  a1=96ebd880  per=800000
    #    IMPORTANT: Use = lookbehind only (not :) to avoid the colon that
    #    separates log-level from C++ file paths in Chromium traces such as
    #    "INFO:cast_content_renderer_client.cc" where "ca" would otherwise
    #    be (incorrectly) matched as a 2-char hex value.  Requiring 6+ chars
    #    provides an additional safety margin.
    ("<HEX>", re.compile(r"(?<=[=])[0-9a-fA-F]{6,}\b")),

    # 7. Hex dump rows:  8+ space-separated 2-hex-digit bytes
    #    e.g.  00 00 00 01 44 2a f8 3b 00 00 00 00 00 00 00 00
    ("<HEXDUMP>", re.compile(r"(?:\b[0-9a-fA-F]{2}\b *){8,}")),

    # 8. journald timestamp + hostname header  (normalise ALL journald lines)
    #    e.g. "Feb 04 18:01:35.779360 LinuxTV " → "<JDT> "
    #    Matches:  Mon  DD  HH:MM:SS.usecs  Hostname
    ("<JDT>", re.compile(
        r"^[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\.\d{3,}\s+\S+"
    )),

    # 9. Kernel dmesg uptime prefix   [ 1469.147499]  /  [  435.831108]
    ("[<UPTIME>]", re.compile(r"^\[\s*\d+\.\d{4,}\]")),

    # 10. journald process-bracket PID  conjure.sh[4728]:  →  conjure.sh[<PID>]:
    #     Lookahead ensures only the PID bracket immediately before : is masked,
    #     not MTK source-line brackets (handled next).
    ("[<PID>]", re.compile(r"\[\d+\](?=:)")),

    # 11. Chromium / Cobalt C++ trace PID:TID  inside square brackets
    #     e.g.  [4728:5459:INFO:spock_protocol.cc(124)]
    #     Replace the numeric PID:TID prefix leaving LEVEL and filename intact.
    ("<PID>:<PID>", re.compile(
        r"(?<=\[)\d+:\d+(?=:(DEBUG|INFO|WARNING|ERROR|VERBOSE|CONSOLE):)"
    )),

    # 12. MTK / Vizio bracketed source-line reference numbers
    #     e.g.  [MDrv_XC_PCMonitor][2006]  [_MTGCEC_GetCommand][1111]
    #     Three-digit minimum avoids masking single-char codes like [0] [1] [E]
    ("[<NUM>]", re.compile(r"\[\d{3,}\]")),

    # 13. ARM backtrace PC / LR / SP register values
    #     e.g.  pc b6f1e044  lr b6f10040  sp bef8ea90
    ("<ADDR>", re.compile(r"\b(?:pc|lr|sp)\s+[0-9a-fA-F]{6,}\b", re.IGNORECASE)),

    # 14. LOKi shared-library path tokens
    #     e.g.  /3rd/loki/libCompanion.so.2
    ("<LIB>", re.compile(r"/3rd/loki/lib\S+\.so(?:\.\d+)*")),

    # 15. Port numbers with explicit prefix
    #     e.g.  port=8080  PORT: 443
    ("<PORT>", re.compile(r"\b(?:port|PORT)\s*[=:]\s*\d{4,5}\b")),

    # 16. Semantic version strings   1.3.10  v2.0.0-rc4
    ("<VER>", re.compile(r"\bv?\d+\.\d+\.\d+(?:[-+]\w+)?\b")),

    # 17. 10-digit UNIX epoch timestamps   1770227734   (2024+)
    ("<TS>", re.compile(r"\b1[4-9]\d{8}\b|\b[2-9]\d{9}\b")),

    # 18. PID / UID / GID keyword=value forms in audit and syslog lines
    #     e.g.  pid=9156  uid=0  auid=4294967295  ppid=1717
    ("<PID>", re.compile(
        r"\b(?:pid|ppid|auid|uid|gid|euid|suid|fsuid|egid|sgid|fsgid|ses)"
        r"\s*[=:]\s*\d+"
    )),

    # 19. Long standalone decimal integers (4+ digits) not already masked
    #     e.g.  type=1300  syscall=397  e_cust_spec_type:155680
    #     Four-digit threshold keeps small constants (0-999) readable.
    ("<NUM>", re.compile(r"\b\d{4,}\b")),
]


def _mask_line(line: str) -> str:
    """Apply all masking rules to a single log line."""
    for token, pattern in _MASK_RULES:
        line = pattern.sub(token, line)
    return line


# ─── Result types ──────────────────────────────────────────────────────────────

@dataclass
class LogTemplate:
    """
    A single log template discovered by DRAIN.

    Attributes:
        template_id: Unique integer identifier (per session).
        template_str: Template string with ``<*>`` wildcards.
        cluster_size: Number of matching lines seen so far.
        first_seen_line: Line number of first occurrence.
        sample_params: Example wildcard values from the last matched line.
    """

    template_id: int
    template_str: str
    cluster_size: int = 0
    first_seen_line: int = 0
    sample_params: list[str] = field(default_factory=list)


@dataclass
class DrainResult:
    """
    Output of :meth:`VizioSpecificDrainAdapter.process_logs`.

    Attributes:
        templates: List of discovered log templates.
        cluster_ids: Per-line template IDs (parallel to input lines).
        novel_template_ids: IDs of templates seen for the first time.
        reduction_ratio: Fraction of input lines represented by templates
            (1.0 = perfect deduplication, 0.0 = no dedup possible).
    """

    templates: list[LogTemplate] = field(default_factory=list)
    cluster_ids: list[int] = field(default_factory=list)
    novel_template_ids: list[int] = field(default_factory=list)
    reduction_ratio: float = 0.0


# ─── Adapter ───────────────────────────────────────────────────────────────────

class VizioSpecificDrainAdapter:
    """
    DRAIN3-based log template clustering tuned for Vizio SmartCast logs.

    Falls back to content-hash grouping when ``drain3`` is not available.

    Key design choices
    ------------------
    * 19-rule pre-masking pipeline strips volatile tokens (timestamps, PIDs,
      addresses, hex values) **before** DRAIN3 sees the line.  This dramatically
      improves template clustering quality for all four log families produced by
      Vizio SmartCast devices:

        - **Kernel dmesg**  (``[ uptime.ns] module: message``)
        - **journald**      (``Mon DD HH:MM:SS.us Hostname process[PID]: …``)
        - **ViziOS bootlog** (``[ViziOS] function(): key=value …``)
        - **LOKi / Chromium C++ traces**

    * ``drain_depth = 5`` gives the DRAIN tree enough levels to route by
      process name (depth 2) and message structure (depths 3-5) without
      becoming too specific.

    * ``parametrize_numeric_tokens = True`` instructs drain3 to widen any
      remaining pure-numeric token into a ``<*>`` wildcard automatically —
      acting as a safety net for decimal values that escape rule 19.

    Example usage::

        adapter = VizioSpecificDrainAdapter()
        result = adapter.process_logs(lines)
        for t in result.templates:
            print(t.template_id, t.template_str)
    """

    def __init__(
        self,
        depth: int = 5,
        sim_threshold: float = 0.4,
        max_clusters: int = 1024,
    ) -> None:
        """
        Args:
            depth: DRAIN tree depth.  5 suits the rich headers in journald /
                dmesg logs (default raised from 4 to 5).
            sim_threshold: Template similarity threshold [0, 1].
            max_clusters: Maximum sustained cluster count.
        """
        self._depth = depth
        self._sim_threshold = sim_threshold
        self._max_clusters = max_clusters
        self._drain_available = False
        self._miner = None
        self._known_template_ids: set[int] = set()

        try:
            from drain3 import TemplateMiner
            from drain3.template_miner_config import TemplateMinerConfig

            cfg = TemplateMinerConfig()
            cfg.drain_depth = depth
            cfg.drain_sim_th = sim_threshold
            cfg.drain_max_clusters = max_clusters
            cfg.masking_instructions = []  # We handle masking ourselves via _mask_line()
            # Auto-wildcard any pure numeric token that slips through pre-masking
            cfg.parametrize_numeric_tokens = True

            self._miner = TemplateMiner(config=cfg)
            self._drain_available = True
            logger.debug("drain3 available – using real DRAIN algorithm")
        except ImportError:
            logger.info(
                "drain3 not installed – falling back to hash-based grouping"
            )

        # Fallback state
        self._template_map: dict[str, int] = {}
        self._templates: list[LogTemplate] = []
        self._next_id: int = 1

    # ── Public API ──────────────────────────────────────────────────────────

    def process_logs(
        self, log_lines: list[str]
    ) -> DrainResult:
        """
        Cluster *log_lines* into templates and return the result.

        Args:
            log_lines: Raw (un-masked) log lines.

        Returns:
            :class:`DrainResult` with templates and per-line cluster IDs.
        """
        cluster_ids: list[int] = []
        novel_ids: list[int] = []

        for line_no, raw in enumerate(log_lines, start=1):
            masked = _mask_line(raw)
            cluster_id, is_novel = self._match(masked, line_no)
            cluster_ids.append(cluster_id)
            if is_novel:
                novel_ids.append(cluster_id)

        unique = len(set(cluster_ids))
        total = len(log_lines)
        reduction = 1.0 - (unique / total) if total > 0 else 0.0

        return DrainResult(
            templates=list(self._templates),
            cluster_ids=cluster_ids,
            novel_template_ids=novel_ids,
            reduction_ratio=reduction,
        )

    def get_novel_templates(self) -> list[LogTemplate]:
        """
        Return templates whose IDs are flagged as first-seen.

        Returns:
            Templates not previously encountered.
        """
        novel = [
            t
            for t in self._templates
            if t.template_id not in self._known_template_ids
        ]
        self._known_template_ids.update(t.template_id for t in novel)
        return novel

    def get_parameters_for_template(
        self, template_id: int
    ) -> list[dict]:
        """
        Return the last-known wildcard parameter values for a template.

        Args:
            template_id: Template integer ID.

        Returns:
            List containing one ``dict`` with ``params`` key; empty list if ID
            not found.
        """
        for t in self._templates:
            if t.template_id == template_id:
                return [{"params": t.sample_params}]
        return []

    # ── Private helpers ─────────────────────────────────────────────────────

    def _match(
        self, masked_line: str, line_no: int
    ) -> tuple[int, bool]:
        """Return (cluster_id, is_novel) for *masked_line*."""
        if self._drain_available and self._miner is not None:
            return self._match_drain(masked_line, line_no)
        return self._match_fallback(masked_line, line_no)

    def _match_drain(
        self, masked_line: str, line_no: int
    ) -> tuple[int, bool]:
        # drain3 v0.9.11 returns a dict:
        #   {change_type, cluster_id, cluster_size, template_mined, cluster_count}
        result = self._miner.add_log_message(masked_line)  # type: ignore[union-attr]
        cid = int(result["cluster_id"])
        change_type = result.get("change_type", "")
        template_str = result.get("template_mined", masked_line)
        is_novel = change_type == "cluster_created"

        # Sync with our template list
        existing = next(
            (t for t in self._templates if t.template_id == cid), None
        )
        if existing is None:
            self._templates.append(
                LogTemplate(
                    template_id=cid,
                    template_str=template_str,
                    cluster_size=1,
                    first_seen_line=line_no,
                )
            )
            is_novel = True
        else:
            existing.cluster_size += 1
            existing.template_str = template_str  # template evolves as new lines arrive

        return cid, is_novel

    def _match_fallback(
        self, masked_line: str, line_no: int
    ) -> tuple[int, bool]:
        """Simple hash-based clustering (no drain3)."""
        # Tokenise and strip numeric tokens to improve grouping
        tokens = masked_line.split()
        key = " ".join(
            t if not re.fullmatch(r"\d+", t) else "<N>" for t in tokens
        )

        if key not in self._template_map:
            tid = self._next_id
            self._next_id += 1
            self._template_map[key] = tid
            self._templates.append(
                LogTemplate(
                    template_id=tid,
                    template_str=key,
                    cluster_size=1,
                    first_seen_line=line_no,
                )
            )
            return tid, True

        tid = self._template_map[key]
        for t in self._templates:
            if t.template_id == tid:
                t.cluster_size += 1
                break
        return tid, False
