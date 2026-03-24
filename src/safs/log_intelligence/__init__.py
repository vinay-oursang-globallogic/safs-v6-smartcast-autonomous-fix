"""
SAFS v6.0 - Log Intelligence Module

Complete log parsing and symbolication for Stages 1-2.

**Main Components**:
- LogIntelligenceAgent: Main orchestrator
- DrainParserAdapter: Log template clustering (95%+ dedup)
- TimestampExtractorAdapter: Multi-format timestamp extraction
- SmartTVErrorAnalyzerAdapter: 5 POC analysis engines
- LokiSymbolicator: ASLR-corrected symbolication
- CDPLogParser: Chrome DevTools Protocol parser
- SourceMapDecoder: JS source map decoder
- MediaTekKernelAnalyzer: Kernel oops/panic analysis

**Usage**:
```python
from safs.log_intelligence import LogIntelligenceAgent, SymbolStore
from safs.bug_layer_router import BugLayer

# Initialize agent
agent = LogIntelligenceAgent(
    symbol_store_path=Path("/opt/safs/symbols"),
)

# Analyze logs
result = await agent.analyze(
    log_lines=filtered_lines,
    bug_layer=BugLayer.LOKI,
    context_keywords=["freeze", "netflix"],
)

# Access results
print(f"Templates: {len(result.drain.templates)}")
print(f"Incidents: {len(result.incidents)}")
print(f"Symbolicated frames: {len(result.loki_symbolication.symbolicated_frames)}")
```
"""

from .agent import LogIntelligenceAgent
from .cdp_parser import (
    CDPLogParser,
    HTML5FrameMapper,
    SourceMapDecoder,
)
from .loki_symbolicator import (
    Addr2LineSymbolicator,
    BacktraceParser,
    LoadMapParser,
    LokiSymbolicator,
    SymbolStore,
)
from .mediatek_parser import (
    HardwareErrorDetector,
    KernelOopsParser,
    MediaTekKernelAnalyzer,
    MediaTekSubsystemClassifier,
)
from .models import (
    Anomaly,
    BacktraceFrame,
    CascadingFailure,
    CDPEvent,
    CDPException,
    CDPParseResult,
    DrainResult,
    EnrichedLogLine,
    ErrorCorrelation,
    ErrorSequence,
    Incident,
    KernelOops,
    LoadMapEntry,
    LogAnalysisResult,
    LogTemplate,
    LokiSymbolicationResult,
    MediaTekKernelResult,
    SourceMapPosition,
    SourceMappedFrame,
    SymbolicatedFrame,
    TimestampFormat,
)
from .poc_adapters import (
    ContextAnalyzerAdapter,
    DrainParserAdapter,
    SmartTVErrorAnalyzerAdapter,
    TimestampExtractorAdapter,
)

__all__ = [
    # Main agent
    "LogIntelligenceAgent",
    # LOKi symbolication
    "LokiSymbolicator",
    "SymbolStore",
    "Addr2LineSymbolicator",
    "LoadMapParser",
    "BacktraceParser",
    # HTML5 parsing
    "CDPLogParser",
    "SourceMapDecoder",
    "HTML5FrameMapper",
    # MediaTek parsing
    "MediaTekKernelAnalyzer",
    "KernelOopsParser",
    "MediaTekSubsystemClassifier",
    "HardwareErrorDetector",
    # POC adapters
    "DrainParserAdapter",
    "TimestampExtractorAdapter",
    "SmartTVErrorAnalyzerAdapter",
    "ContextAnalyzerAdapter",
    # Models
    "LogAnalysisResult",
    "DrainResult",
    "LogTemplate",
    "EnrichedLogLine",
    "TimestampFormat",
    "ErrorCorrelation",
    "ErrorSequence",
    "Incident",
    "Anomaly",
    "CascadingFailure",
    "LokiSymbolicationResult",
    "LoadMapEntry",
    "BacktraceFrame",
    "SymbolicatedFrame",
    "CDPParseResult",
    "CDPEvent",
    "CDPException",
    "SourceMapPosition",
    "SourceMappedFrame",
    "KernelOops",
    "MediaTekKernelResult",
]
