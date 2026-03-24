"""
Test Suite for Phase 7: Repository Locator (Stage 4).

Covers four-path retrieval, rate limiting, temporal decay, and repo locator agent.

Master Prompt Reference: Section 4 - Retrieval System
"""

import pytest
import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, Mock, MagicMock
from src.safs.retrieval import (
    RepositoryAdapter,
    GitHubMCPAdapter,
    GitLabAdapter,
    FileChange,
    CommitInfo,
    SearchResult,
    PriorityRateLimiter,
    Priority,
    TemporallyWeightedRetrieval,
    ErrorCategory,
    DECAY_HALFLIFE,
    RetrievalRouter,
    GitHubMCPClient,
    CodeIndexMCPClient,
)
from src.safs.agents import RepoLocatorAgent, RepoLocatorResult, CodeLocation
from src.safs.log_analysis.models import RootCauseResult, MistakeSeverity, ErrorCategory as ModelErrorCategory


# ============================================================================
# TestPriorityRateLimiter
# ============================================================================


@pytest.mark.asyncio
class TestPriorityRateLimiter:
    """Test rate limiter with priority budget sharding."""

    async def test_p0_p1_primary_shard(self):
        """P0/P1 requests consume from primary shard first."""
        limiter = PriorityRateLimiter(p0_p1_budget=2, p2_p3_budget=1)
        
        # First 2 P0 requests should succeed (primary shard)
        assert await limiter.acquire(Priority.P0) is True
        assert await limiter.acquire(Priority.P1) is True
        
        # Third P0 request should burst into secondary shard
        assert await limiter.acquire(Priority.P0) is True
        
        # Fourth P0 request should fail (both shards exhausted)
        assert await limiter.acquire(Priority.P0) is False

    async def test_p2_p3_secondary_only(self):
        """P2/P3 requests can only consume from secondary shard."""
        limiter = PriorityRateLimiter(p0_p1_budget=5, p2_p3_budget=1)
        
        # P2 request consumes from secondary shard
        assert await limiter.acquire(Priority.P2) is True
        
        # Second P2 request fails (secondary exhausted, cannot burst into primary)
        assert await limiter.acquire(Priority.P2) is False

    async def test_rolling_window_cleanup(self):
        """Old calls are removed from rolling window."""
        limiter = PriorityRateLimiter(
            p0_p1_budget=1,
            p2_p3_budget=1,
            window_seconds=1,  # 1 second window for testing
        )
        
        # Consume budget
        assert await limiter.acquire(Priority.P0) is True
        assert await limiter.acquire(Priority.P2) is True
        
        # Both shards exhausted
        assert await limiter.acquire(Priority.P0) is False
        
        # Wait for window to expire
        await asyncio.sleep(1.2)
        
        # Budget should be refreshed
        assert await limiter.acquire(Priority.P0) is True

    async def test_wait_for_capacity(self):
        """wait_for_capacity waits until budget available."""
        limiter = PriorityRateLimiter(
            p0_p1_budget=1,
            p2_p3_budget=0,
            window_seconds=1,
        )
        
        # Consume budget
        assert await limiter.acquire(Priority.P0) is True
        
        # Start waiting (should succeed after 1 second)
        start = asyncio.get_event_loop().time()
        await limiter.wait_for_capacity(Priority.P0, timeout=5.0)
        elapsed = asyncio.get_event_loop().time() - start
        
        assert elapsed >= 1.0  # Waited at least 1 second

    async def test_wait_for_capacity_timeout(self):
        """wait_for_capacity times out if capacity never available."""
        limiter = PriorityRateLimiter(
            p0_p1_budget=1,
            p2_p3_budget=0,
            window_seconds=10,  # Long window
        )
        
        # Consume budget
        assert await limiter.acquire(Priority.P0) is True
        
        # Wait should timeout
        with pytest.raises(asyncio.TimeoutError):
            await limiter.wait_for_capacity(Priority.P0, timeout=0.5)

    async def test_usage_stats(self):
        """get_usage_stats returns accurate usage information."""
        limiter = PriorityRateLimiter(p0_p1_budget=5, p2_p3_budget=3)
        
        # Consume some budget
        await limiter.acquire(Priority.P0)
        await limiter.acquire(Priority.P1)
        await limiter.acquire(Priority.P2)
        
        stats = await limiter.get_usage_stats()
        
        assert stats["p0_p1_used"] == 2
        assert stats["p0_p1_available"] == 3
        assert stats["p2_p3_used"] == 1
        assert stats["p2_p3_available"] == 2


# ============================================================================
# TestTemporallyWeightedRetrieval
# ============================================================================


class TestTemporallyWeightedRetrieval:
    """Test temporal decay for institutional memory."""

    def test_decay_weight_recent(self):
        """Recent fixes have weight ~1.0."""
        ranker = TemporallyWeightedRetrieval()
        
        weight = ranker.decay_weight(age_days=0, category=ErrorCategory.COMPANION_LIB_TIMING)
        
        assert weight == 1.0

    def test_decay_weight_half_life(self):
        """At half-life, weight should be 0.5."""
        ranker = TemporallyWeightedRetrieval()
        
        # COMPANION_LIB_TIMING has 90-day half-life
        weight = ranker.decay_weight(age_days=90, category=ErrorCategory.COMPANION_LIB_TIMING)
        
        assert weight == pytest.approx(0.5, abs=0.01)

    def test_decay_weight_old(self):
        """Old fixes have significantly lower weight."""
        ranker = TemporallyWeightedRetrieval()
        
        # 2x half-life
        weight = ranker.decay_weight(age_days=180, category=ErrorCategory.COMPANION_LIB_TIMING)
        
        assert weight < 0.4

    def test_category_specific_half_lives(self):
        """Different categories have different decay rates."""
        ranker = TemporallyWeightedRetrieval()
        
        # Hardware (730d half-life) decays slowly
        hw_weight = ranker.decay_weight(age_days=100, category=ErrorCategory.LOKI_SEGFAULT)
        
        # Memory (30d half-life) decays quickly
        mem_weight = ranker.decay_weight(age_days=100, category=ErrorCategory.MEMORY_LEAK)
        
        assert hw_weight > mem_weight

    def test_rerank_applies_decay(self):
        """rerank applies temporal decay to search results."""
        ranker = TemporallyWeightedRetrieval()
        
        now = datetime.now(timezone.utc)
        results = [
            {
                "id": "recent",
                "score": 0.8,
                "fixed_at": now - timedelta(days=1),
            },
            {
                "id": "old",
                "score": 0.8,
                "fixed_at": now - timedelta(days=180),
            },
        ]
        
        reranked = ranker.rerank(results, ErrorCategory.COMPANION_LIB_TIMING)
        
        # Recent fix should rank higher
        assert reranked[0]["id"] == "recent"
        assert reranked[0]["final_score"] > reranked[1]["final_score"]

    def test_rerank_enriches_metadata(self):
        """rerank adds decay metadata to results."""
        ranker = TemporallyWeightedRetrieval()
        
        now = datetime.now(timezone.utc)
        results = [
            {
                "id": "test",
                "score": 0.8,
                "fixed_at": now - timedelta(days=30),
            },
        ]
        
        reranked = ranker.rerank(results, ErrorCategory.COMPANION_LIB_TIMING)
        
        assert "original_score" in reranked[0]
        assert "decay_weight" in reranked[0]
        assert "age_days" in reranked[0]
        assert "final_score" in reranked[0]
        assert reranked[0]["original_score"] == 0.8


# ============================================================================
# TestRepositoryAdapter
# ============================================================================


@pytest.mark.asyncio
class TestGitHubMCPAdapter:
    """Test GitHub MCP adapter."""

    async def test_get_file(self):
        """get_file retrieves file contents via GitHub MCP."""
        mock_mcp = AsyncMock()
        mock_mcp.call = AsyncMock(return_value={"content": "test content"})
        
        adapter = GitHubMCPAdapter(mock_mcp)
        content = await adapter.get_file("owner/repo", "path/to/file.py")
        
        assert content == "test content"
        mock_mcp.call.assert_called_once()

    async def test_search_code(self):
        """search_code returns search results."""
        mock_mcp = AsyncMock()
        mock_mcp.call = AsyncMock(
            return_value={
                "items": [
                    {
                        "repository": {"full_name": "owner/repo"},
                        "path": "src/main.py",
                        "text_matches": [{"fragment": "def main():"}],
                    }
                ]
            }
        )
        
        adapter = GitHubMCPAdapter(mock_mcp)
        results = await adapter.search_code("main function")
        
        assert len(results) == 1
        assert results[0].repo == "owner/repo"
        assert results[0].path == "src/main.py"

    async def test_list_commits(self):
        """list_commits returns commit history."""
        mock_mcp = AsyncMock()
        mock_mcp.call = AsyncMock(
            return_value={
                "commits": [
                    {
                        "sha": "abc123",
                        "commit": {
                            "message": "Fix bug",
                            "author": {"name": "Dev", "date": "2024-01-01"},
                        },
                        "files": [{"filename": "file.py"}],
                    }
                ]
            }
        )
        
        adapter = GitHubMCPAdapter(mock_mcp)
        commits = await adapter.list_commits("owner/repo")
        
        assert len(commits) == 1
        assert commits[0].sha == "abc123"
        assert commits[0].message == "Fix bug"


@pytest.mark.asyncio
class TestGitLabAdapter:
    """Test GitLab adapter."""

    async def test_get_file(self):
        """get_file retrieves file contents via GitLab API."""
        mock_client = AsyncMock()
        mock_resp = Mock()
        mock_resp.json = Mock(return_value={"content": "dGVzdCBjb250ZW50"})  # "test content" base64
        mock_resp.raise_for_status = Mock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        
        adapter = GitLabAdapter("https://gitlab.com", "token")
        adapter.client = mock_client
        
        content = await adapter.get_file("group/project", "file.py")
        
        assert content == "test content"

    async def test_create_branch(self):
        """create_branch creates new branch via GitLab API."""
        mock_client = AsyncMock()
        mock_resp = Mock()
        mock_resp.json = Mock(return_value={"name": "feature-branch"})
        mock_resp.raise_for_status = Mock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        
        adapter = GitLabAdapter("https://gitlab.com", "token")
        adapter.client = mock_client
        
        branch = await adapter.create_branch("group/project", "feature-branch", "main")
        
        assert branch == "feature-branch"


# ============================================================================
# TestRetrievalRouter
# ============================================================================


@pytest.mark.asyncio
class TestRetrievalRouter:
    """Test retrieval router orchestration."""

    async def test_get_file_path_a_success(self):
        """get_file uses PATH A if rate limit available."""
        # Mock GitHub MCP client's call method directly
        mock_github_client = GitHubMCPClient(None)
        mock_github_client.call = AsyncMock(return_value={"content": "file content"})
        
        router = RetrievalRouter(github_mcp=mock_github_client)
        
        content = await router.get_file("owner/repo", "file.py", priority=Priority.P1)
        
        assert content == "file content"

    async def test_get_file_path_b_fallback(self):
        """get_file falls back to PATH B if PATH A rate limited."""
        # Mock GitHub MCP client
        mock_github_client = GitHubMCPClient(None)
        mock_github_client.call = AsyncMock(return_value={"content": "github content"})
        
        # Mock Code Index MCP client
        mock_code_client = CodeIndexMCPClient(None)
        mock_code_client.call = AsyncMock(return_value={"content": "fallback content"})
        
        # Rate limiter with no budget
        rate_limiter = PriorityRateLimiter(p0_p1_budget=0, p2_p3_budget=0)
        
        router = RetrievalRouter(
            github_mcp=mock_github_client,
            code_index_mcp=mock_code_client,
            rate_limiter=rate_limiter,
        )
        
        content = await router.get_file("owner/repo", "file.py", priority=Priority.P1)
        
        assert content == "fallback content"

    async def test_semantic_code_search(self):
        """semantic_code_search uses PATH B (no rate limits)."""
        # Mock Code Index MCP client
        mock_code_client = CodeIndexMCPClient(None)
        mock_code_client.call = AsyncMock(
            return_value={
                "results": [
                    {
                        "repo": "owner/repo",
                        "path": "src/main.py",
                        "content": "def main():",
                        "line_number": 10,
                    }
                ]
            }
        )
        
        router = RetrievalRouter(code_index_mcp=mock_code_client)
        
        results = await router.semantic_code_search("main function")
        
        assert len(results) == 1
        assert results[0].repo == "owner/repo"

    async def test_symbol_search(self):
        """symbol_search uses PATH B for exact AST matches."""
        # Mock Code Index MCP client
        mock_code_client = CodeIndexMCPClient(None)
        mock_code_client.call = AsyncMock(
            return_value={
                "results": [
                    {
                        "repo": "owner/repo",
                        "path": "src/utils.py",
                        "definition": "def process_log():",
                        "line_number": 42,
                    }
                ]
            }
        )
        
        router = RetrievalRouter(code_index_mcp=mock_code_client)
        
        results = await router.symbol_search("process_log", "function")
        
        assert len(results) == 1
        assert results[0].path == "src/utils.py"

    async def test_find_similar_fixes(self):
        """find_similar_fixes queries PATH C with temporal decay."""
        mock_qdrant = AsyncMock()
        mock_qdrant.search = AsyncMock(
            return_value=[
                {
                    "repo": "owner/repo",
                    "file_path": "src/bug.py",
                    "score": 0.9,
                    "fixed_at": datetime.now(timezone.utc) - timedelta(days=30),
                }
            ]
        )
        
        router = RetrievalRouter(qdrant_client=mock_qdrant)
        
        fixes = await router.find_similar_fixes(
            "segmentation fault",
            ErrorCategory.LOKI_SEGFAULT,
        )
        
        assert len(fixes) == 1
        assert "final_score" in fixes[0]
        assert "decay_weight" in fixes[0]

    async def test_get_device_info(self):
        """get_device_info queries PATH D (vizio-ssh)."""
        mock_vizio_ssh = AsyncMock()
        mock_vizio_ssh.call = AsyncMock(
            return_value={
                "device_id": "DEV001",
                "firmware_version": "5.2.1",
                "model": "P-Series",
            }
        )
        
        router = RetrievalRouter(vizio_ssh_mcp=mock_vizio_ssh)
        
        device_info = await router.get_device_info("DEV001")
        
        assert device_info["firmware_version"] == "5.2.1"


# ============================================================================
# TestRepoLocatorAgent
# ============================================================================


@pytest.mark.asyncio
class TestRepoLocatorAgent:
    """Test Repository Locator Agent (Stage 4)."""

    async def test_extract_symbols(self):
        """_extract_symbols extracts functions, classes, files from root cause."""
        agent = RepoLocatorAgent(retrieval_router=Mock())
        
        root_cause = RootCauseResult(
            root_cause="Bug in process_video() function in video_decoder.cpp",
            confidence=0.85,
            error_category=ModelErrorCategory.LOKI_SEGFAULT_NULL_DEREF,
            severity=MistakeSeverity.HIGH,
            affected_files=["video_decoder.cpp", "VideoDecoder", "AudioSync"],
        )
        
        symbols = agent._extract_symbols(root_cause)
        
        assert "process_video" in symbols
        assert "video_decoder.cpp" in symbols
        assert "VideoDecoder" in symbols

    async def test_locate_multi_path(self):
        """locate orchestrates four-path retrieval."""
        # Mock RetrievalRouter
        mock_router = AsyncMock()
        
        # PATH B - Symbol search
        mock_router.symbol_search = AsyncMock(
            return_value=[
                SearchResult(
                    repo="vizio/smartcast",
                    path="src/video/decoder.cpp",
                    content="void process_video() {",
                    line_number=42,
                )
            ]
        )
        
        # PATH A - Code search
        mock_router.search_code = AsyncMock(
            return_value=[
                SearchResult(
                    repo="vizio/smartcast",
                    path="src/video/pipeline.cpp",
                    content="video pipeline error",
                    line_number=100,
                )
            ]
        )
        
        # PATH B - Semantic search
        mock_router.semantic_code_search = AsyncMock(return_value=[])
        
        # PATH C - Similar fixes
        mock_router.find_similar_fixes = AsyncMock(
            return_value=[
                {
                    "repo": "vizio/smartcast",
                    "file_path": "src/video/decoder.cpp",
                    "final_score": 0.7,
                    "fix_summary": "Fixed video decoder crash",
                }
            ]
        )
        
        # PATH C - Known mistakes
        mock_router.find_known_mistakes = AsyncMock(return_value=[])
        
        # PATH D - Device info
        mock_router.get_device_info = AsyncMock(
            return_value={"firmware_version": "5.2.1"}
        )
        
        agent = RepoLocatorAgent(retrieval_router=mock_router)
        
        root_cause = RootCauseResult(
            root_cause="Video decoder crash in process_video()",
            confidence=0.85,
            error_category=ModelErrorCategory.LOKI_SEGFAULT_NULL_DEREF,
            severity=MistakeSeverity.HIGH,
            affected_files=["VideoDecoder"],
        )
        
        result = await agent.locate(
            root_cause=root_cause,
            category=ErrorCategory.LOKI_SEGFAULT,
            device_id="DEV001",
        )
        
        assert isinstance(result, RepoLocatorResult)
        assert len(result.primary_locations) > 0
        assert result.device_context is not None
        assert result.confidence_score > 0.0

    async def test_locate_deduplicates_locations(self):
        """locate deduplicates same repo+path, keeping highest confidence."""
        mock_router = AsyncMock()
        mock_router.symbol_search = AsyncMock(
            return_value=[
                SearchResult(
                    repo="vizio/smartcast",
                    path="src/main.cpp",
                    content="code",
                    line_number=10,
                ),
                SearchResult(
                    repo="vizio/smartcast",
                    path="src/main.cpp",
                    content="code",
                    line_number=20,
                ),
            ]
        )
        mock_router.search_code = AsyncMock(return_value=[])
        mock_router.semantic_code_search = AsyncMock(return_value=[])
        mock_router.find_similar_fixes = AsyncMock(return_value=[])
        mock_router.find_known_mistakes = AsyncMock(return_value=[])
        mock_router.get_device_info = AsyncMock(return_value=None)
        
        agent = RepoLocatorAgent(retrieval_router=mock_router)
        
        root_cause = RootCauseResult(
            root_cause="Bug in main",
            confidence=0.85,
            error_category=ModelErrorCategory.LOKI_SEGFAULT_NULL_DEREF,
            severity=MistakeSeverity.MEDIUM,
            affected_files=["main"],
        )
        
        result = await agent.locate(
            root_cause=root_cause,
            category=ErrorCategory.UNKNOWN,
        )
        
        # Should deduplicate to 1 location
        assert len(result.primary_locations) == 1

    async def test_locate_ranks_by_confidence(self):
        """locate ranks locations by confidence score."""
        mock_router = AsyncMock()
        
        # PATH B symbol (high confidence 0.85)
        mock_router.symbol_search = AsyncMock(
            return_value=[
                SearchResult(
                    repo="vizio/smartcast",
                    path="high_conf.cpp",
                    content="code",
                    line_number=1,
                )
            ]
        )
        
        # PATH A search (medium confidence 0.65)
        mock_router.search_code = AsyncMock(
            return_value=[
                SearchResult(
                    repo="vizio/smartcast",
                    path="low_conf.cpp",
                    content="code",
                    line_number=2,  # Different line to avoid dedup
                )
            ]
        )
        
        mock_router.semantic_code_search = AsyncMock(return_value=[])
        mock_router.find_similar_fixes = AsyncMock(return_value=[])
        mock_router.find_known_mistakes = AsyncMock(return_value=[])
        mock_router.get_device_info = AsyncMock(return_value=None)
        
        agent = RepoLocatorAgent(retrieval_router=mock_router)
        
        root_cause = RootCauseResult(
            root_cause="bug in process_something()",
            confidence=0.85,
            error_category=ModelErrorCategory.LOKI_SEGFAULT_NULL_DEREF,
            severity=MistakeSeverity.MEDIUM,
            affected_files=[],
        )
        
        result = await agent.locate(
            root_cause=root_cause,
            category=ErrorCategory.UNKNOWN,
        )
        
        # Should have multiple locations
        assert len(result.primary_locations) >= 1
        
        # High confidence location should be present with higher score
        high_conf_loc = next((loc for loc in result.primary_locations if "high_conf" in loc.path), None)
        assert high_conf_loc is not None
        assert high_conf_loc.confidence >= 0.85


# ============================================================================
# Integration Tests
# ============================================================================


@pytest.mark.asyncio
class TestIntegration:
    """End-to-end integration tests."""

    async def test_full_retrieval_flow(self):
        """Test complete four-path retrieval flow."""
        # Setup mock clients
        mock_github_mcp_conn = AsyncMock()
        mock_code_index_conn = AsyncMock()
        mock_qdrant = AsyncMock()
        
        mock_github_mcp = GitHubMCPClient(mock_github_mcp_conn)
        mock_code_index = CodeIndexMCPClient(mock_code_index_conn)
        
        # Mock responses
        mock_github_mcp_conn.call_tool = AsyncMock(
            return_value={"content": "github file content"}
        )
        mock_code_index_conn.call_tool = AsyncMock(
            return_value={"results": []}
        )
        mock_qdrant.search = AsyncMock(return_value=[])
        
        # Initialize router and agent
        router = RetrievalRouter(
            github_mcp=mock_github_mcp,
            code_index_mcp=mock_code_index,
            qdrant_client=mock_qdrant,
        )
        
        agent = RepoLocatorAgent(retrieval_router=router)
        
        # Execute full flow
        root_cause = RootCauseResult(
            root_cause="Crash in decode_frame() in decoder.cpp",
            confidence=0.85,
            error_category=ModelErrorCategory.LOKI_SEGFAULT_NULL_DEREF,
            severity=MistakeSeverity.HIGH,
            affected_files=["decoder.cpp", "VideoDecoder"],
        )
        
        result = await agent.locate(
            root_cause=root_cause,
            category=ErrorCategory.LOKI_SEGFAULT,
        )
        
        assert isinstance(result, RepoLocatorResult)
        assert result.confidence_score >= 0.0
