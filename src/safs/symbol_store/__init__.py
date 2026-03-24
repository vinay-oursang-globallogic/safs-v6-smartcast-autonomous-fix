"""
Symbol Store Module
===================

MinIO/S3 storage for debug symbols (.so.debug ELFs, .js.map files).

Components:
- store.py: MinIO/S3 client wrapper with local filesystem fallback
"""

from .store import SymbolStoreClient, SymbolStoreError
from .elf_symbolication import ElfSymbolicator, SymbolicationStatus, SymbolicatedFrame
from .source_map_decoder import SourceMapStore, SourceMapPosition

# Re-export SymbolStore from log_intelligence for backward compat
from safs.log_intelligence.loki_symbolicator import SymbolStore

__all__ = [
    "SymbolStore",
    "SymbolStoreClient",
    "SymbolStoreError",
    "ElfSymbolicator",
    "SymbolicationStatus",
    "SymbolicatedFrame",
    "SourceMapStore",
    "SourceMapPosition",
]
