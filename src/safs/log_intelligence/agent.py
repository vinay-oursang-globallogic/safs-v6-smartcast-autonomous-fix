"""
SAFS v6.0 - Log Intelligence Agent

Main orchestrator for Stages 1-2: Log Parsing + Symbolication.

**Pipeline**:
1. Drain clustering (95%+ log deduplication)
2. Timestamp enrichment (multi-format support)
3. POC SmartTVErrorAnalyzer (5 analysis engines):
   - Temporal correlations
   - Incident detection (60s gap clustering)
   - Anomaly detection (3x baseline spikes)
   - Cascading failure chains
   - Heuristic root cause inference
4. Layer-specific parsing (dispatched by BugLayer):
   - LOKI → ASLR symbolication + addr2line
   - HTML5 → CDP trace parsing + source maps
   - MEDIATEK → Kernel oops parsing + subsystem classification

**Output**: LogAnalysisResult with complete parsed/symbolicated data

**Usage**:
```python
agent = LogIntelligenceAgent(
    symbol_store_path=Path("/opt/safs/symbols"),
    source_maps={"bundle.js": decoder},
)

result = await agent.analyze(
    log_lines=filtered_lines,
    bug_layer=BugLayer.LOKI,
    context_keywords=["freeze", "netflix"],
)
```
"""

from pathlib import Path
from typing import Optional

from ..log_analysis.models import BugLayer
from .cdp_parser import CDPLogParser, HTML5FrameMapper, SourceMapDecoder
from .loki_symbolicator import LokiSymbolicator, SymbolStore
from .mediatek_parser import MediaTekKernelAnalyzer
from .models import (
    CDPParseResult,
    DrainResult,
    EnrichedLogLine,
    LogAnalysisResult,
    LokiSymbolicationResult,
    MediaTekKernelResult,
    SourceMappedFrame,
)
from .poc_adapters import (
    DrainParserAdapter,
    SmartTVErrorAnalyzerAdapter,
    TimestampExtractorAdapter,
)


# ==================================================================================
# LOG INTELLIGENCE AGENT
# ==================================================================================


class LogIntelligenceAgent:
    """
    Main log intelligence orchestrator for SAFS v6.0.

    Dispatches to layer-specific parsers and combines POC analysis engines
    with new symbolication/parsing components.
    """

    def __init__(
        self,
        symbol_store_path: Optional[Path] = None,
        source_maps: Optional[dict[str, SourceMapDecoder]] = None,
        drain_similarity: float = 0.5,
        drain_max_examples: int = 3,
    ):
        """
        Initialize Log Intelligence Agent.

        Args:
            symbol_store_path: Path to debug symbol store (for LOKi symbolication)
            source_maps: Dict of {minified_url → SourceMapDecoder} (for HTML5 source mapping)
            drain_similarity: Similarity threshold for Drain clustering (0.0-1.0)
            drain_max_examples: Max example logs to store per template
        """
        # POC adapters
        self.drain_adapter = DrainParserAdapter(
            similarity_threshold=drain_similarity,
            max_examples=drain_max_examples,
        )
        self.timestamp_adapter = TimestampExtractorAdapter()

        # Layer-specific parsers
        # LOKi
        if symbol_store_path:
            symbol_store = SymbolStore(symbol_store_path)
            self.loki_symbolicator = LokiSymbolicator(symbol_store)
        else:
            self.loki_symbolicator = None

        # HTML5
        self.cdp_parser = CDPLogParser()
        self.source_maps = source_maps or {}
        self.html5_frame_mapper = HTML5FrameMapper(self.source_maps) if source_maps else None

        # MediaTek
        self.mediatek_analyzer = MediaTekKernelAnalyzer()

    async def analyze(
        self,
        log_lines: list[str],
        bug_layer: BugLayer,
        context_keywords: Optional[list[str]] = None,
        log_file_path: Optional[Path] = None,
    ) -> LogAnalysisResult:
        """
        Analyze log lines with full intelligence pipeline.

        Args:
            log_lines: Filtered log lines (post-Quality Gate)
            bug_layer: Bug layer classification (LOKI, HTML5, MEDIATEK, CROSS_LAYER, UNKNOWN)
            context_keywords: Keywords from Jira ticket description (e.g., ["freeze", "netflix"])
            log_file_path: Optional source log file path (for kernel uptime conversion)

        Returns:
            LogAnalysisResult with complete analysis
        """
        # Phase 1: Drain template clustering
        drain_result = self.drain_adapter.process_logs(log_lines)

        # Phase 2: Timestamp enrichment
        enriched_lines = self.timestamp_adapter.enrich_logs(
            log_lines, log_file_path
        )

        # Phase 3: POC SmartTVErrorAnalyzer (5 analysis engines)
        analyzer_adapter = SmartTVErrorAnalyzerAdapter(
            context_keywords=context_keywords
        )
        (
            correlations,
            incidents,
            anomalies,
            cascading_failures,
            heuristic_root_causes,
        ) = analyzer_adapter.analyze(enriched_lines)

        # Phase 4: Layer-specific parsing
        loki_symbolication = None
        cdp_analysis = None
        source_mapped_frames = None
        mediatek_analysis = None

        if bug_layer == BugLayer.LOKI:
            loki_symbolication = await self._symbolicate_loki(log_lines)

        elif bug_layer == BugLayer.HTML5:
            cdp_analysis, source_mapped_frames = await self._parse_html5(
                log_lines
            )

        elif bug_layer == BugLayer.MEDIATEK:
            mediatek_analysis = self._parse_mediatek(log_lines)

        elif bug_layer == BugLayer.CROSS_LAYER:
            # CROSS_LAYER: Run both LOKi and HTML5 parsing
            loki_symbolication = await self._symbolicate_loki(log_lines)
            cdp_analysis, source_mapped_frames = await self._parse_html5(
                log_lines
            )

        # Assemble final result
        return LogAnalysisResult(
            drain=drain_result,
            enriched_lines=enriched_lines,
            correlations=correlations,
            incidents=incidents,
            anomalies=anomalies,
            cascading_failures=cascading_failures,
            heuristic_root_causes=heuristic_root_causes,
            loki_symbolication=loki_symbolication,
            cdp_analysis=cdp_analysis,
            source_mapped_frames=source_mapped_frames,
            mediatek_analysis=mediatek_analysis,
        )

    async def _symbolicate_loki(
        self, log_lines: list[str]
    ) -> Optional[LokiSymbolicationResult]:
        """LOKi symbolication (ASLR + addr2line)"""
        if not self.loki_symbolicator:
            return None

        try:
            result = await self.loki_symbolicator.symbolicate(log_lines)
            return result
        except Exception:
            # Graceful degradation: return None if symbolication fails
            return None

    async def _parse_html5(
        self, log_lines: list[str]
    ) -> tuple[Optional[CDPParseResult], Optional[list[SourceMappedFrame]]]:
        """HTML5 CDP parsing + source mapping"""
        cdp_analysis = None
        source_mapped_frames = None

        try:
            # Find CDP JSON in log lines
            cdp_json = self._extract_cdp_json(log_lines)
            if cdp_json:
                cdp_analysis = self.cdp_parser.parse(cdp_json)

                # Map exceptions to original source
                if self.html5_frame_mapper and cdp_analysis.exceptions:
                    source_mapped = []
                    for exception in cdp_analysis.exceptions:
                        frames = self.html5_frame_mapper.map_exception(exception)
                        source_mapped.extend(frames)
                    source_mapped_frames = source_mapped

        except Exception:
            pass

        return cdp_analysis, source_mapped_frames

    def _parse_mediatek(
        self, log_lines: list[str]
    ) -> Optional[MediaTekKernelResult]:
        """MediaTek kernel oops/panic analysis"""
        try:
            result = self.mediatek_analyzer.analyze(log_lines)
            return result
        except Exception:
            return None

    def _extract_cdp_json(self, log_lines: list[str]) -> Optional[str]:
        """
        Extract CDP JSON from log lines.

        CDP traces can be:
        1. Embedded in log lines: "CDP_TRACE: {\"method\":...}"
        2. Separate JSON file (not in log lines)

        Returns:
            CDP JSON string or None
        """
        cdp_buffer = []
        in_cdp_block = False

        for line in log_lines:
            # Start of CDP block
            if "CDP_TRACE:" in line or '"method":' in line:
                in_cdp_block = True
                # Extract JSON part after "CDP_TRACE:"
                if "CDP_TRACE:" in line:
                    json_start = line.index("CDP_TRACE:") + len("CDP_TRACE:")
                    cdp_buffer.append(line[json_start:].strip())
                else:
                    cdp_buffer.append(line.strip())
                continue

            # Continuation of CDP block
            if in_cdp_block:
                # End of JSON block (heuristic: line doesn't start with space/tab)
                if line.strip() and not line[0] in [" ", "\t", "{", "}", "[", "]"]:
                    break
                cdp_buffer.append(line.strip())

        if cdp_buffer:
            cdp_json = "\n".join(cdp_buffer)
            # Validate JSON
            try:
                import json

                json.loads(cdp_json)
                return cdp_json
            except json.JSONDecodeError:
                pass

        return None
