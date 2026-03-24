"""
Qdrant Collections Module
==========================

Vector database setup and indexing for institutional memory.

Components:
- collection_setup.py: Creates both hybrid collections (BM25 + dense)
- institutional_memory.py: RRF fusion + temporal decay retrieval
- models.py: Pydantic models for records and queries

Collections:
- historical_fixes: Past successful fixes with diffs
- fix_corrections: Past mistakes, developer corrections, regressions
"""

from .collection_setup import QdrantCollectionManager
from .fix_indexer import FixIndexer
from .correction_indexer import CorrectionIndexer
from .institutional_memory import (
    InstitutionalMemory,
    RRFFusion,
    TemporalDecayRanker,
)
from .models import (
    CorrectionRecord,
    FixRecord,
    RRFFusionConfig,
    SearchQuery,
    SearchResult,
    TemporalDecayConfig,
)

__all__ = [
    # Collection management
    "QdrantCollectionManager",
    # Write path
    "FixIndexer",
    "CorrectionIndexer",
    # Retrieval
    "InstitutionalMemory",
    "RRFFusion",
    "TemporalDecayRanker",
    # Models
    "FixRecord",
    "CorrectionRecord",
    "SearchQuery",
    "SearchResult",
    "RRFFusionConfig",
    "TemporalDecayConfig",
]

