"""
Unit tests for PRCreatorAgent — Phase 14

Tests PR creation functionality including:
- Basic PR creation with RepositoryAdapter
- CROSS_LAYER support (dual PR creation)
- Retry logic for API failures
- PR body formatting with all evidence
"""

import pytest
from unittest.mock import AsyncMock, Mock, patch
from datetime import datetime, timezone

from safs.agents.pr_creator import PRCreatorAgent, PRResult
from safs.log_analysis.models import (
    PipelineState,
    JiraTicket,
    BugLayer,
    BugLayerResult,
    FixCandidate,
    FixStrategy,
    ConfidenceRouting,
    RootCauseResult,
    ErrorCategory,
    MistakeSeverity,
)
from safs.agents.confidence_ensemble import ConfidenceResult
from safs.retrieval.retrieval_router import RetrievalRouter
from safs.retrieval.repository_adapter import RepositoryAdapter, FileChange


class MockRepositoryAdapter(RepositoryAdapter):
    """Mock adapter for testing."""
    
    def __init__(self):
        self.create_branch_called = False
        self.push_files_called = False
        self.create_pr_called = False
        self.call_count = 0
        self.should_fail_times = 0
    
    async def get_file(self, repo: str, path: str, ref: str = "main") -> str:
        return "mock content"
    
    async def search_code(self, query: str, language=None):
        return []
    
    async def list_commits(self, repo: str, path=None, since=None):
        return []
    
    async def create_branch(self, repo: str, branch: str, from_ref: str) -> str:
        self.call_count += 1
        if self.call_count <= self.should_fail_times:
            raise Exception(f"Simulated failure {self.call_count}")
        self.create_branch_called = True
        return branch
    
    async def push_files(self, repo: str, branch: str, files: list) -> str:
        self.call_count += 1
        if self.call_count <= self.should_fail_times:
            raise Exception(f"Simulated failure {self.call_count}")
        self.push_files_called = True
        return "abc123"
    
    async def create_pull_request(
        self, repo: str, title: str, body: str,
        head: str, base: str, draft: bool = True
    ) -> str:
        self.call_count += 1
        if self.call_count <= self.should_fail_times:
            raise Exception(f"Simulated failure {self.call_count}")
        self.create_pr_called = True
        assert draft is True, "PRs must always be draft per Master Prompt Rule #7"
        return f"https://github.com/{repo}/pull/123"


@pytest.fixture
def mock_router():
    """Create mock RetrievalRouter."""
    router = Mock(spec=RetrievalRouter)
    adapter = MockRepositoryAdapter()
    router.get_adapter = Mock(return_value=adapter)
    return router, adapter


@pytest.fixture
def sample_state():
    """Create sample pipeline state."""
    ticket = JiraTicket(key="SMART-12345", summary="Test bug")
    state = PipelineState(ticket=ticket)
    state.buglayer_result = BugLayerResult(
        layer=BugLayer.LOKI,
        confidence=0.9,
        layer_scores={BugLayer.LOKI: 0.9},
        matched_patterns=["LOKI_CRASH"],
    )
    state.root_cause_result = RootCauseResult(
        root_cause="Null pointer dereference in video decoder",
        error_category=ErrorCategory.LOKI_SEGFAULT_NULL_DEREF,
        severity=MistakeSeverity.HIGH,
        confidence=0.85,
        affected_files=["src/decoder.c", "include/decoder.h"],
    )
    return state


@pytest.fixture
def sample_candidate():
    """Create sample fix candidate."""
    return FixCandidate(
        strategy=FixStrategy.NULL_CHECK,
        confidence=0.9,
        routing=ConfidenceRouting.AUTO_PR,
        summary="Add null check before decoder access",
        explanation="Added null pointer check to prevent crash when codec data is missing",
        diff="+ if (codec_data != NULL) { ... }",
        file_changes=[
            {"path": "src/decoder.c", "content": "fixed code", "operation": "update"}
        ],
        target_repo="vizio/SmartCast",
        target_branch="main",
    )


@pytest.mark.asyncio
async def test_pr_creator_basic(mock_router, sample_state, sample_candidate):
    """Test basic PR creation."""
    router, adapter = mock_router
    pr_creator = PRCreatorAgent(retrieval_router=router)
    
    result = await pr_creator.create(
        state=sample_state,
        candidate=sample_candidate,
    )
    
    assert result.pr_url == "https://github.com/vizio/SmartCast/pull/123"
    assert result.branch_name.startswith("safs/smart-12345/")
    assert adapter.create_branch_called
    assert adapter.push_files_called
    assert adapter.create_pr_called


@pytest.mark.asyncio
async def test_pr_creator_with_retry(mock_router, sample_state, sample_candidate):
    """Test retry logic on API failures."""
    router, adapter = mock_router
    adapter.should_fail_times = 2  # Fail first 2 attempts
    
    pr_creator = PRCreatorAgent(
        retrieval_router=router,
        max_retries=3,
        retry_delay=0.1,  # Fast retry for testing
    )
    
    result = await pr_creator.create(
        state=sample_state,
        candidate=sample_candidate,
    )
    
    # Should succeed on 3rd attempt
    assert result.pr_url == "https://github.com/vizio/SmartCast/pull/123"
    assert adapter.call_count > 2  # Multiple attempts were made


@pytest.mark.asyncio
async def test_pr_creator_max_retries_exceeded(mock_router, sample_state, sample_candidate):
    """Test that operation fails after max retries."""
    router, adapter = mock_router
    adapter.should_fail_times = 10  # Always fail
    
    pr_creator = PRCreatorAgent(
        retrieval_router=router,
        max_retries=3,
        retry_delay=0.1,
    )
    
    with pytest.raises(Exception, match="Simulated failure"):
        await pr_creator.create(
            state=sample_state,
            candidate=sample_candidate,
        )


@pytest.mark.asyncio
async def test_pr_creator_cross_layer(mock_router, sample_state):
    """Test CROSS_LAYER PR creation (creates 2 PRs)."""
    router, adapter = mock_router
    
    # Update state to CROSS_LAYER
    sample_state.buglayer_result.layer = BugLayer.CROSS_LAYER
    
    # Create candidate with secondary fix
    candidate = FixCandidate(
        strategy=FixStrategy.NULL_CHECK,
        confidence=0.9,
        routing=ConfidenceRouting.AUTO_PR,
        summary="Fix LOKi layer crash",
        explanation="Added null check in LOKi",
        file_changes=[
            {"path": "src/loki.c", "content": "fixed loki code", "operation": "update"}
        ],
        target_repo="vizio/LOKi",
        has_secondary_fix=True,
        secondary_repo="vizio/SmartCast-HTML5",
        secondary_summary="Fix HTML5 companion issue",
        secondary_file_changes=[
            {"path": "src/player.js", "content": "fixed html5 code", "operation": "update"}
        ],
    )
    
    pr_creator = PRCreatorAgent(retrieval_router=router)
    
    result = await pr_creator.create(
        state=sample_state,
        candidate=candidate,
    )
    
    # Should have both primary and secondary PR URLs
    assert result.pr_url is not None
    assert result.secondary_pr_url is not None
    assert result.secondary_branch is not None
    
    # Adapter should be called multiple times (for both PRs)
    assert adapter.call_count >= 6  # 3 ops × 2 PRs


@pytest.mark.asyncio
async def test_pr_body_formatting(mock_router, sample_state, sample_candidate):
    """Test PR body includes all evidence."""
    from safs.agents.confidence_ensemble import ConfidenceSignals
    router, _ = mock_router
    pr_creator = PRCreatorAgent(retrieval_router=router)
    
    signals = ConfidenceSignals(
        llm_confidence=0.9,
        retrieval_similarity=0.8,
        validation_score=0.85,
    )
    confidence = ConfidenceResult(
        raw_score=0.88,
        calibrated_score=0.92,
        routing=ConfidenceRouting.AUTO_PR,
        signals=signals,
    )
    
    body = pr_creator._build_pr_body(
        state=sample_state,
        candidate=sample_candidate,
        validation=None,
        repro=None,
        confidence=confidence,
    )
    
    # Check that body includes key sections
    assert "SAFS v6.0 Automated Fix" in body
    assert "SMART-12345" in body
    assert "Root Cause Analysis" in body
    assert "Null pointer dereference" in body
    assert "Fix Explanation" in body
    assert "Confidence Assessment" in body
    assert "92" in body  # Matches both "92%" and "92.0%"
    assert "DRAFT" in body


@pytest.mark.asyncio
async def test_pr_creator_no_router_fails(sample_state, sample_candidate):
    """Test that PR creation fails without retrieval_router."""
    pr_creator = PRCreatorAgent()  # No router provided
    
    with pytest.raises(ValueError, match="RetrievalRouter required"):
        await pr_creator.create(
            state=sample_state,
            candidate=sample_candidate,
        )


@pytest.mark.asyncio
async def test_pr_title_truncation(mock_router, sample_state):
    """Test that PR title is truncated to 80 chars."""
    router, _ = mock_router
    pr_creator = PRCreatorAgent(retrieval_router=router)
    
    # Create candidate with very long summary
    candidate = FixCandidate(
        strategy=FixStrategy.NULL_CHECK,
        confidence=0.9,
        routing=ConfidenceRouting.AUTO_PR,
        summary="A" * 200,  # Very long summary
        target_repo="vizio/SmartCast",
        file_changes=[{"path": "test.c", "content": "test", "operation": "update"}],
    )
    
    title = pr_creator._build_pr_title("SMART-123", candidate)
    
    # Title should be truncated (max 100 chars)
    assert len(title) <= 100


def test_file_change_conversion():
    """Test conversion of dict to FileChange objects."""
    pr_creator = PRCreatorAgent()
    
    file_changes = [
        {"path": "file1.c", "content": "content1", "operation": "update"},
        {"path": "file2.c", "content": "content2", "operation": "create"},
    ]
    
    result = pr_creator._convert_to_file_changes(file_changes)
    
    assert len(result) == 2
    assert all(isinstance(fc, FileChange) for fc in result)
    assert result[0].path == "file1.c"
    assert result[0].content == "content1"
    assert result[0].operation == "update"


@pytest.mark.asyncio
async def test_branch_name_generation(mock_router, sample_state, sample_candidate):
    """Test that branch names follow correct format."""
    router, _ = mock_router
    pr_creator = PRCreatorAgent(retrieval_router=router)
    
    branch = pr_creator._generate_branch_name("SMART-456", sample_candidate)
    
    # Should follow format: safs/{ticket}/{strategy}-{date}
    assert branch.startswith("safs/smart-456/")
    assert "null_check" in branch.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
