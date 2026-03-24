"""
Retrieval module - Four-path retrieval architecture.

NEW in v6.0 — multi-platform repository abstraction with intelligent
rate limiting, semantic search, and temporal decay.

Master Prompt Reference: Section 4 - Retrieval System
"""

from .repository_adapter import (
    RepositoryAdapter,
    GitHubMCPAdapter,
    GitLabAdapter,
    FileChange,
    CommitInfo,
    SearchResult,
)
from .rate_limiter import (
    PriorityRateLimiter,
    Priority,
    CallRecord,
)
from .temporal_ranker import (
    TemporallyWeightedRetrieval,
    ErrorCategory,
    DECAY_HALFLIFE,
)
from .retrieval_router import RetrievalRouter
from .github_mcp_client import GitHubMCPClient
from .code_index_mcp_client import CodeIndexMCPClient
from .circuit_breaker import CircuitBreaker, CircuitState, CircuitOpenError

__all__ = [
    # Repository Adapters
    "RepositoryAdapter",
    "GitHubMCPAdapter",
    "GitLabAdapter",
    "FileChange",
    "CommitInfo",
    "SearchResult",
    
    # Rate Limiting
    "PriorityRateLimiter",
    "Priority",
    "CallRecord",
    
    # Temporal Ranking
    "TemporallyWeightedRetrieval",
    "ErrorCategory",
    "DECAY_HALFLIFE",
    
    # Retrieval Router
    "RetrievalRouter",
    
    # MCP Clients
    "GitHubMCPClient",
    "CodeIndexMCPClient",
    # Circuit Breaker
    "CircuitBreaker",
    "CircuitState",
    "CircuitOpenError",
]
