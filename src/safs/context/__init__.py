"""
Context Module
==============

Context assembly, TF-IDF scoring, MinHash deduplication.

Ported from mcp_server_jira_log_analyzer POC.

Components:
- context_builder.py: Assembles context for fix generation (Stage 5)
- tfidf_scorer.py: Rare event scoring (from POC)
- minhash_dedup.py: Fuzzy deduplication (from POC)
- context_analyzer.py: Context relevance mapping (from POC)
- chunk_merger.py: Merges overlapping code chunks
- syntax_compressor.py: Optional LLMLingua-2 fallback (only if context > 150K tokens) [NOT IMPLEMENTED]
"""

from .context_builder import ContextBuilderAgent
from .tfidf_scorer import TFIDFScorer
from .minhash_dedup import MinHashDeduplicator
from .context_analyzer import ContextAnalyzer
from .chunk_merger import ChunkMerger, CodeChunk
from .syntax_compressor import SyntaxAwareCompressor

__all__ = [
    "ContextBuilderAgent",
    "TFIDFScorer",
    "MinHashDeduplicator",
    "ContextAnalyzer",
    "ChunkMerger",
    "CodeChunk",
    "SyntaxAwareCompressor",
]
