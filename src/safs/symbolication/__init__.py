"""
Symbolication Module
====================

ASLR correction, ELF symbol lookup, CDP parsing, and source map decoding.

Built new for v6.0 (not in POC).

Components:
- loki_log_parser.py: Linux process crash format + LOKi log prefixes
- loki_symbolicator.py: ASLR correction, Build-ID ELF, addr2line
- cdp_parser.py: Chrome DevTools Protocol JSON trace parser
- source_map_decoder.py: JS source map → original file:line:col
- mtk_kernel_parser.py: dmesg/kernel oops + MTK subsystem classifier
"""

from .loki_symbolicator import LokiSymbolicator

__all__ = [
    "LokiLogParser",
    "LokiSymbolicator",
    "CDPParser",
    "SourceMapDecoder",
    "MTKKernelParser",
]
