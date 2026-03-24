"""
Retrieval Router - Central orchestrator for four-path retrieval.

NEW in v6.0 — coordinates all retrieval paths with rate limiting,
circuit breakers, and temporal decay.

Master Prompt Reference: Section 4.3 - RetrievalRouter
"""

import re
from typing import Any, Optional
from .repository_adapter import RepositoryAdapter, GitHubMCPAdapter, GitLabAdapter, SearchResult
from .rate_limiter import PriorityRateLimiter, Priority
from .temporal_ranker import TemporallyWeightedRetrieval, ErrorCategory
from .github_mcp_client import GitHubMCPClient
from .code_index_mcp_client import CodeIndexMCPClient


class RetrievalRouter:
    """
    Central router for all retrieval operations.
    
    Four-Path Architecture (Master Prompt Section 4.1):
    - PATH A: RepositoryAdapter (GitHub/GitLab/Bitbucket) - exact search, writes
    - PATH B: Code-Index-MCP (PVC) - semantic search, no rate limits
    - PATH C: Qdrant Institutional Memory - historical fixes, known mistakes
    - PATH D: On-Device Registry (vizio-ssh) - live firmware/version info
    
    Circuit Breaker:
    If PATH A exhausts rate limit → fallback to PATH B
    """

    def __init__(
        self,
        github_mcp: Optional[GitHubMCPClient] = None,
        gitlab_url: Optional[str] = None,
        gitlab_token: Optional[str] = None,
        code_index_mcp: Optional[CodeIndexMCPClient] = None,
        qdrant_client: Optional[Any] = None,
        vizio_ssh_mcp: Optional[Any] = None,
        rate_limiter: Optional[PriorityRateLimiter] = None,
        temporal_ranker: Optional[TemporallyWeightedRetrieval] = None,
    ):
        """
        Initialize retrieval router.
        
        Args:
            github_mcp: GitHub MCP client
            gitlab_url: GitLab instance URL
            gitlab_token: GitLab access token
            code_index_mcp: Code-Index-MCP client
            qdrant_client: Qdrant client for PATH C
            vizio_ssh_mcp: Vizio SSH MCP client for PATH D
            rate_limiter: Rate limiter for PATH A
            temporal_ranker: Temporal ranker for PATH C
        """
        self.github_adapter: Optional[RepositoryAdapter] = None
        if github_mcp:
            self.github_adapter = GitHubMCPAdapter(github_mcp)
        
        self.gitlab_adapter: Optional[RepositoryAdapter] = None
        if gitlab_url and gitlab_token:
            self.gitlab_adapter = GitLabAdapter(gitlab_url, gitlab_token)
        
        self.code_index_mcp = code_index_mcp
        self.qdrant_client = qdrant_client
        self.vizio_ssh_mcp = vizio_ssh_mcp
        
        self.rate_limiter = rate_limiter or PriorityRateLimiter()
        self.temporal_ranker = temporal_ranker or TemporallyWeightedRetrieval()

    def get_adapter(self, repo: str) -> Optional[RepositoryAdapter]:
        """
        Get appropriate adapter based on repository pattern.
        
        Pattern Matching:
        - github.com/* → GitHubMCPAdapter
        - gitlab.com/* → GitLabAdapter
        - Custom GitLab instances → GitLabAdapter
        
        Args:
            repo: Repository identifier
        
        Returns:
            Appropriate adapter or None
        """
        if "github.com" in repo or "/" in repo and not "gitlab" in repo:
            return self.github_adapter
        elif "gitlab" in repo:
            return self.gitlab_adapter
        else:
            # Default to GitHub adapter
            return self.github_adapter

    async def get_file(
        self,
        repo: str,
        path: str,
        ref: str = "main",
        priority: Priority = Priority.P2,
    ) -> Optional[str]:
        """
        Get file contents via PATH A with rate limiting.
        
        Circuit Breaker:
        If PATH A rate limited → fallback to PATH B (Code-Index-MCP)
        
        Args:
            repo: Repository identifier
            path: File path
            ref: Git ref
            priority: Request priority
        
        Returns:
            File contents or None if not found
        """
        adapter = self.get_adapter(repo)
        if not adapter:
            return None
        
        # Try PATH A with rate limiting
        if await self.rate_limiter.acquire(priority):
            try:
                return await adapter.get_file(repo, path, ref)
            except Exception as e:
                # Circuit breaker: Rate limit error
                if "rate limit" in str(e).lower():
                    # Fallback to PATH B
                    return await self._get_file_code_index(repo, path)
                raise
        else:
            # Rate limit exhausted → immediate fallback to PATH B
            return await self._get_file_code_index(repo, path)

    async def _get_file_code_index(self, repo: str, path: str) -> Optional[str]:
        """Fallback file retrieval via Code-Index-MCP (PATH B)."""
        if not self.code_index_mcp:
            return None
        
        try:
            result = await self.code_index_mcp.call("get_file", repo=repo, path=path)
            return result.get("content")
        except Exception:
            return None

    async def search_code(
        self,
        query: str,
        language: Optional[str] = None,
        priority: Priority = Priority.P2,
    ) -> list[SearchResult]:
        """
        Search code via PATH A with rate limiting.
        
        Fallback to PATH B semantic search if rate limited.
        
        Args:
            query: Search query
            language: Optional language filter
            priority: Request priority
        
        Returns:
            List of search results
        """
        # Try PATH A
        if await self.rate_limiter.acquire(priority):
            adapter = self.github_adapter or self.gitlab_adapter
            if adapter:
                try:
                    return await adapter.search_code(query, language)
                except Exception as e:
                    if "rate limit" in str(e).lower():
                        # Fallback to PATH B
                        return await self.semantic_code_search(query, language)
                    raise
        
        # Rate limited → PATH B
        return await self.semantic_code_search(query, language)

    async def semantic_code_search(
        self,
        query: str,
        language: Optional[str] = None,
        top_k: int = 10,
    ) -> list[SearchResult]:
        """
        Semantic code search via PATH B (Code-Index-MCP).
        
        NO RATE LIMITS - PVC-backed semantic search.
        
        Args:
            query: Natural language or code query
            language: Optional language filter
            top_k: Number of results
        
        Returns:
            Semantic search results
        """
        if not self.code_index_mcp:
            return []
        
        # Handle mock vs real client
        if hasattr(self.code_index_mcp, 'semantic_search'):
            result = await self.code_index_mcp.semantic_search(query, language, top_k)
        else:
            result = await self.code_index_mcp.call("semantic_search", query=query, language=language, top_k=top_k)
        
        search_results = []
        for item in result.get("results", []):
            search_results.append(
                SearchResult(
                    repo=item.get("repo", ""),
                    path=item.get("path", ""),
                    content=item.get("content", ""),
                    line_number=item.get("line_number"),
                )
            )
        
        return search_results

    async def symbol_search(
        self, symbol: str, symbol_type: Optional[str] = None
    ) -> list[SearchResult]:
        """
        AST-based symbol search via PATH B.
        
        NO RATE LIMITS - direct AST indexing.
        
        Args:
            symbol: Symbol name (function, class, variable)
            symbol_type: Optional type filter
        
        Returns:
            Exact symbol matches
        """
        if not self.code_index_mcp:
            return []
        
        # Handle mock vs real client
        if hasattr(self.code_index_mcp, 'symbol_search'):
            result = await self.code_index_mcp.symbol_search(symbol, symbol_type)
        else:
            result = await self.code_index_mcp.call("symbol_search", symbol=symbol, symbol_type=symbol_type)
        
        search_results = []
        for item in result.get("results", []):
            search_results.append(
                SearchResult(
                    repo=item.get("repo", ""),
                    path=item.get("path", ""),
                    content=item.get("definition", ""),
                    line_number=item.get("line_number"),
                )
            )
        
        return search_results

    async def find_similar_fixes(
        self,
        query: str,
        category: ErrorCategory,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """
        Find similar historical fixes via PATH C (Qdrant).
        
        Applies temporal decay to down-rank old fixes.
        
        Args:
            query: Query string (error message, stack trace)
            category: Error category for temporal decay
            top_k: Number of results
        
        Returns:
            Historical fixes with temporal decay applied
        """
        if not self.qdrant_client:
            return []
        
        # Query Qdrant (uses Phase 4 institutional memory)
        results = await self.qdrant_client.search(
            collection_name="historical_fixes",
            query_text=query,
            limit=top_k,
        )
        
        # Apply temporal decay
        decay_results = self.temporal_ranker.rerank(
            results,
            category=category,
            score_key="score",
            date_key="fixed_at",
        )
        
        return decay_results

    async def find_known_mistakes(
        self,
        query: str,
        category: ErrorCategory,
        top_k: int = 3,
    ) -> list[dict[str, Any]]:
        """
        Find known mistakes from PATH C (Qdrant).
        
        Master Prompt Rule #25:
        Known mistakes = previous failed fixes that devs explicitly marked.
        
        Args:
            query: Query string
            category: Error category
            top_k: Number of results
        
        Returns:
            Known mistakes with temporal decay
        """
        if not self.qdrant_client:
            return []
        
        results = await self.qdrant_client.search(
            collection_name="known_mistakes",
            query_text=query,
            limit=top_k,
        )
        
        # Apply temporal decay
        decay_results = self.temporal_ranker.rerank(
            results,
            category=category,
            score_key="score",
            date_key="reported_at",
        )
        
        return decay_results

    async def get_device_info(
        self, device_id: str
    ) -> Optional[dict[str, Any]]:
        """
        Get live device info via PATH D (vizio-ssh MCP).
        
        Retrieves firmware version, running processes, config from on-device registry.
        
        Args:
            device_id: Device identifier
        
        Returns:
            Device information or None
        """
        if not self.vizio_ssh_mcp:
            return None
        
        try:
            result = await self.vizio_ssh_mcp.call("get_device_info", device_id=device_id)
            return result
        except Exception:
            return None

    async def list_commits(
        self,
        repo: str,
        path: Optional[str] = None,
        since: Optional[str] = None,
        priority: Priority = Priority.P2,
    ) -> list:
        """
        List commits via PATH A with rate limiting.
        
        Args:
            repo: Repository identifier
            path: Optional file path filter
            since: Optional timestamp filter
            priority: Request priority
        
        Returns:
            List of commits
        """
        adapter = self.get_adapter(repo)
        if not adapter:
            return []
        
        if await self.rate_limiter.acquire(priority):
            return await adapter.list_commits(repo, path, since)
        else:
            # Rate limited, return empty
            return []

    async def create_branch(
        self,
        repo: str,
        branch: str,
        from_ref: str,
        priority: Priority = Priority.P0,
    ) -> Optional[str]:
        """Create branch via PATH A (write operation)."""
        adapter = self.get_adapter(repo)
        if not adapter:
            return None
        
        if await self.rate_limiter.acquire(priority):
            return await adapter.create_branch(repo, branch, from_ref)
        else:
            # Write operations MUST wait for capacity
            await self.rate_limiter.wait_for_capacity(priority, timeout=120.0)
            return await adapter.create_branch(repo, branch, from_ref)

    async def push_files(
        self,
        repo: str,
        branch: str,
        files: list,
        priority: Priority = Priority.P0,
    ) -> Optional[str]:
        """Push files via PATH A (write operation)."""
        adapter = self.get_adapter(repo)
        if not adapter:
            return None
        
        if await self.rate_limiter.acquire(priority):
            return await adapter.push_files(repo, branch, files)
        else:
            # Write operations MUST wait
            await self.rate_limiter.wait_for_capacity(priority, timeout=120.0)
            return await adapter.push_files(repo, branch, files)

    async def create_pull_request(
        self,
        repo: str,
        title: str,
        body: str,
        head: str,
        base: str,
        draft: bool = True,
        priority: Priority = Priority.P0,
    ) -> Optional[str]:
        """Create PR via PATH A (write operation)."""
        adapter = self.get_adapter(repo)
        if not adapter:
            return None
        
        if await self.rate_limiter.acquire(priority):
            return await adapter.create_pull_request(
                repo, title, body, head, base, draft
            )
        else:
            # Write operations MUST wait
            await self.rate_limiter.wait_for_capacity(priority, timeout=120.0)
            return await adapter.create_pull_request(
                repo, title, body, head, base, draft
            )
