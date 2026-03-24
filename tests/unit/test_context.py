"""
SAFS v6.0 — Phase 8 Tests: Context Builder

Comprehensive test suite for Context Builder (Stage 5).

Test Coverage:
- TFIDFScorer: Keyword extraction, rare event scoring
- MinHashDeduplicator: Fuzzy deduplication, similarity detection
- ContextAnalyzer: Keyword mapping, relevance scoring
- ChunkMerger: Overlap detection, chunk merging
- ContextBuilderAgent: End-to-end context assembly
"""

import pytest
from datetime import datetime, timezone
from typing import List

from src.safs.context import (
    TFIDFScorer,
    MinHashDeduplicator,
    ContextAnalyzer,
    ChunkMerger,
    CodeChunk,
    ContextBuilderAgent,
)
from src.safs.log_analysis.models import (
    JiraTicket,
   ErrorCategory,
    MistakeSeverity,
    RootCauseResult,
    PipelineState,
    QualityResult,
    BugLayerResult,
    BugLayer,
)
from src.safs.agents.repo_locator import RepoLocatorResult, CodeLocation


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture
def sample_log_lines():
    """Sample log lines for TF-IDF testing."""
    return [
        "[ERROR] SIGSEGV at address 0x0 in malloc",
        "[ERROR] NULL pointer dereference in AppLauncher::Init",
        "[WARN] Memory allocation failed, size=1024MB",
        "[INFO] Starting video decoder init",
        "[ERROR] SIGSEGV segmentation fault detected",
        "[ERROR] Backtrace: AppLauncher.cpp:142",
    ]


@pytest.fixture
def sample_chunks():
    """Sample code chunks for testing."""
    return [
        CodeChunk(
            repo="vizio/smartcast-loki",
            file_path="src/app/AppLauncher.cpp",
            start_line=100,
            end_line=120,
            content="void AppLauncher::Init() {\n  if (ptr == nullptr) {\n    // ERROR\n  }\n}",
            source="path_a",
            confidence=0.85,
        ),
        CodeChunk(
            repo="vizio/smartcast-loki",
            file_path="src/app/AppLauncher.cpp",
            start_line=115,
            end_line=135,
            content="void AppLauncher::Launch() {\n  Init();\n  // Launch app\n}",
            source="path_b",
            confidence=0.75,
        ),
    ]


@pytest.fixture
def sample_ticket():
    """Sample Jira ticket."""
    return JiraTicket(
        key="TVPF-12345",
        summary="App crashes on launch",
        description="The app freezes when I try to launch Netflix. Black screen appears.",
        priority="high",
        status="Open",
        created=datetime.now(timezone.utc),
        updated=datetime.now(timezone.utc),
        raw_logs=["[ERROR] SIGSEGV"],
    )


@pytest.fixture
def sample_root_cause():
    """Sample root cause result."""
    return RootCauseResult(
        root_cause="NULL pointer dereference in AppLauncher::Init() at line 142",
        confidence=0.85,
        error_category=ErrorCategory.LOKI_SEGFAULT_NULL_DEREF,
        severity=MistakeSeverity.HIGH,
        affected_files=["AppLauncher.cpp"],
    )


# ============================================================================
# TEST TFIDF SCORER
# ============================================================================

class TestTFIDFScorer:
    """Test TF-IDF keyword extraction."""
    
    def test_initialization(self):
        """Test TFIDFScorer initialization."""
        scorer = TFIDFScorer()
        assert scorer.min_word_length == 3
        assert scorer.max_word_length == 50
        assert len(scorer.STOP_WORDS) > 0
    
    def test_tokenize(self):
        """Test text tokenization."""
        scorer = TFIDFScorer()
        tokens = scorer._tokenize("Hello WORLD 123 test_func error")
        
        assert "hello" in tokens
        assert "world" in tokens
        assert "123" in tokens
        assert "test_func" in tokens
        # "error" is a stop word, should be filtered
        assert "error" not in tokens
    
    def test_compute_tf(self):
        """Test term frequency computation."""
        scorer = TFIDFScorer()
        tokens = ["hello", "world", "hello", "test"]
        
        tf = scorer._compute_tf(tokens)
        
        assert tf["hello"] == 0.5  # 2/4
        assert tf["world"] == 0.25  # 1/4
        assert tf["test"] == 0.25  # 1/4
    
    def test_compute_idf(self):
        """Test IDF computation."""
        scorer = TFIDFScorer()
        documents = [
            ["hello", "world"],
            ["hello", "test"],
            ["goodbye", "world"],
        ]
        
        idf = scorer._compute_idf(documents)
        
        # "hello" appears in 2/3 docs, IDF = log(3/2)
        assert 0.3 < idf["hello"] < 0.5
        
        # "goodbye" appears in 1/3 docs, IDF = log(3/1) > IDF("hello")
        assert idf["goodbye"] > idf["hello"]
    
    def test_extract_keywords(self, sample_log_lines):
        """Test keyword extraction from logs."""
        scorer = TFIDFScorer()
        keywords = scorer.extract_keywords(sample_log_lines, top_k=5)
        
        assert len(keywords) > 0
        assert all(isinstance(kw, str) for kw, _ in keywords)
        assert all(isinstance(score, float) for _, score in keywords)
        
        # Check that "sigsegv" or "malloc" appears (rare terms)
        keyword_list = [kw for kw, _ in keywords]
        assert any(kw in ["sigsegv", "malloc", "segmentation"] for kw in keyword_list)
    
    def test_extract_keywords_empty(self):
        """Test keyword extraction with empty input."""
        scorer = TFIDFScorer()
        keywords = scorer.extract_keywords([], top_k=5)
        assert keywords == []


# ============================================================================
# TEST MINHASH DEDUPLICATOR
# ============================================================================

class TestMinHashDeduplicator:
    """Test MinHash fuzzy deduplication."""
    
    def test_initialization(self):
        """Test MinHashDeduplicator initialization."""
        dedup = MinHashDeduplicator(threshold=0.8, num_perm=128)
        assert dedup.threshold == 0.8
        assert dedup.num_perm == 128
        assert dedup.shingle_size == 3
    
    def test_normalize(self):
        """Test text normalization."""
        dedup = MinHashDeduplicator()
        normalized = dedup._normalize("  Hello   WORLD  \n  Test  ")
        assert normalized == "hello world test"
    
    def test_shingle(self):
        """Test k-shingle generation."""
        dedup = MinHashDeduplicator(shingle_size=3)
        shingles = dedup._shingle("hello")
        
        assert "hel" in shingles
        assert "ell" in shingles
        assert "llo" in shingles
        assert len(shingles) == 3
    
    def test_compute_minhash(self):
        """Test MinHash signature computation."""
        dedup = MinHashDeduplicator(num_perm=64)
        shingles = {"abc", "bcd", "cde"}
        
        signature = dedup._compute_minhash(shingles)
        
        assert len(signature) == 64
        assert all(isinstance(h, int) for h in signature)
    
    def test_jaccard_similarity_identical(self):
        """Test Jaccard similarity for identical texts."""
        dedup = MinHashDeduplicator()
        
        text1 = "This is a test message"
        text2 = "This is a test message"
        
        similarity = dedup.compute_similarity(text1, text2)
        assert similarity == 1.0
    
    def test_jaccard_similarity_different(self):
        """Test Jaccard similarity for different texts."""
        dedup = MinHashDeduplicator()
        
        text1 = "This is a test message"
        text2 = "Completely different content here"
        
        similarity = dedup.compute_similarity(text1, text2)
        assert similarity < 0.3
    
    def test_deduplicate_exact_duplicates(self):
        """Test deduplication of exact duplicates."""
        dedup = MinHashDeduplicator(threshold=0.9)
        
        texts = [
            "This is a test",
            "This is a test",
            "This is another test",
        ]
        
        groups = dedup.deduplicate(texts, return_groups=True)
        
        # Should have 2 groups: [0,1] and [2]
        assert len(groups) == 2
    
    def test_deduplicate_near_duplicates(self):
        """Test deduplication of near-duplicates."""
        dedup = MinHashDeduplicator(threshold=0.8)
        
        texts = [
            "NULL pointer dereference in AppLauncher",
            "NULL pointer dereference in AppLauncher at line 142",
            "Segmentation fault detected",
        ]
        
        groups = dedup.deduplicate(texts, return_groups=True)
        
        # First two should be grouped together (similar)
        assert len(groups) <= 2


# ============================================================================
# TEST CONTEXT ANALYZER
# ============================================================================

class TestContextAnalyzer:
    """Test context relevance analysis."""
    
    def test_initialization(self):
        """Test ContextAnalyzer initialization."""
        analyzer = ContextAnalyzer()
        assert len(analyzer.KEYWORD_MAPPINGS) > 0
        assert len(analyzer.KEYWORD_WEIGHTS) > 0
    
    def test_extract_keywords_freeze(self):
        """Test keyword extraction for 'freeze' symptom."""
        analyzer = ContextAnalyzer()
        keywords = analyzer.extract_keywords("The app freezes when I launch it")
        
        assert "deadlock" in keywords or "hang" in keywords
        assert "timeout" in keywords
    
    def test_extract_keywords_crash(self):
        """Test keyword extraction for 'crash' symptom."""
        analyzer = ContextAnalyzer()
        keywords = analyzer.extract_keywords("The TV crashes and reboots")
        
        assert ("segfault" in keywords or "sigsegv" in keywords or 
                "kernel_panic" in keywords)
    
    def test_extract_keywords_black_screen(self):
        """Test keyword extraction for 'black_screen' symptom."""
        analyzer = ContextAnalyzer()
        keywords = analyzer.extract_keywords("I see a black screen, no video")
        
        assert ("display_init" in keywords or "gpu_fault" in keywords or 
                "video_decode" in keywords)
    
    def test_extract_keywords_empty(self):
        """Test keyword extraction with empty description."""
        analyzer = ContextAnalyzer()
        keywords = analyzer.extract_keywords("")
        assert keywords == []
    
    def test_score_relevance_high(self):
        """Test relevance scoring with high relevance."""
        analyzer = ContextAnalyzer()
        
        code = "void AppLauncher::Init() { if (ptr == nullptr) segfault(); }"
        root_cause = "NULL pointer dereference in AppLauncher::Init()"
        keywords = ["segfault", "null", "pointer"]
        
        score = analyzer.score_relevance(code, root_cause, keywords)
        
        assert score > 0.5  # Should be highly relevant
    
    def test_score_relevance_low(self):
        """Test relevance scoring with low relevance."""
        analyzer = ContextAnalyzer()
        
        code = "void Logger::log(string msg) { cout << msg; }"
        root_cause = "NULL pointer dereference in AppLauncher::Init()"
        keywords = ["segfault", "null", "pointer"]
        
        score = analyzer.score_relevance(code, root_cause, keywords)
        
        assert score < 0.3  # Should be less relevant


# ============================================================================
# TEST CHUNK MERGER
# ============================================================================

class TestChunkMerger:
    """Test code chunk merging."""
    
    def test_initialization(self):
        """Test ChunkMerger initialization."""
        merger = ChunkMerger(context_lines=5, max_gap=5)
        assert merger.context_lines == 5
        assert merger.max_gap == 5
    
    def test_chunk_overlaps(self):
        """Test overlap detection."""
        chunk1 = CodeChunk(
            repo="test", file_path="test.cpp",
            start_line=100, end_line=120,
            content="", source="a", confidence=0.8,
        )
        chunk2 = CodeChunk(
            repo="test", file_path="test.cpp",
            start_line=115, end_line=135,
            content="", source="b", confidence=0.7,
        )
        
        assert chunk1.overlaps(chunk2)
        assert chunk2.overlaps(chunk1)
    
    def test_chunk_not_overlaps(self):
        """Test non-overlapping chunks."""
        chunk1 = CodeChunk(
            repo="test", file_path="test.cpp",
            start_line=100, end_line=120,
            content="", source="a", confidence=0.8,
        )
        chunk2 = CodeChunk(
            repo="test", file_path="test.cpp",
            start_line=150, end_line=170,
            content="", source="b", confidence=0.7,
        )
        
        assert not chunk1.overlaps(chunk2)
    
    def test_chunk_adjacent(self):
        """Test adjacent chunk detection."""
        chunk1 = CodeChunk(
            repo="test", file_path="test.cpp",
            start_line=100, end_line=120,
            content="", source="a", confidence=0.8,
        )
        chunk2 = CodeChunk(
            repo="test", file_path="test.cpp",
            start_line=122, end_line=140,
            content="", source="b", confidence=0.7,
        )
        
        assert chunk1.adjacent(chunk2, max_gap=5)
    
    def test_merge_overlapping_chunks(self, sample_chunks):
        """Test merging overlapping chunks."""
        merger = ChunkMerger()
        merged = merger.merge_chunks(sample_chunks)
        
        # Two overlapping chunks should merge into one
        assert len(merged) == 1
        assert merged[0].start_line == 100
        assert merged[0].end_line == 135
    
    def test_deduplicate_chunks(self):
        """Test chunk deduplication."""
        merger = ChunkMerger()
        
        chunks = [
            CodeChunk(
                repo="test", file_path="test.cpp",
                start_line=100, end_line=120,
                content="code", source="a", confidence=0.8,
            ),
            CodeChunk(
                repo="test", file_path="test.cpp",
                start_line=100, end_line=120,
                content="code", source="b", confidence=0.9,  # Higher confidence
            ),
        ]
        
        deduplicated = merger.deduplicate_chunks(chunks)
        
        # Should keep only one (with higher confidence)
        assert len(deduplicated) == 1
        assert deduplicated[0].confidence == 0.9


# ============================================================================
# TEST CONTEXT BUILDER AGENT
# ============================================================================

@pytest.mark.asyncio
class TestContextBuilderAgent:
    """Test Context Builder Agent (Stage 5)."""
    
    async def test_initialization(self):
        """Test ContextBuilderAgent initialization."""
        from unittest.mock import Mock
        
        router = Mock()
        agent = ContextBuilderAgent(router)
        
        assert agent.router == router
        assert agent.max_chunks == 10
        assert isinstance(agent.analyzer, ContextAnalyzer)
        assert isinstance(agent.merger, ChunkMerger)
        assert isinstance(agent.deduplicator, MinHashDeduplicator)
        assert isinstance(agent.tfidf, TFIDFScorer)
    
    async def test_locations_to_chunks(self):
        """Test converting CodeLocation to CodeChunk."""
        from unittest.mock import Mock
        
        router = Mock()
        agent = ContextBuilderAgent(router)
        
        locations = [
            CodeLocation(
                repo="test",
                path="test.cpp",
                line_number=100,
                confidence=0.85,
                source="path_a",
                content_preview="void func() { code; }",
            ),
        ]
        
        chunks = await agent._locations_to_chunks(locations)
        
        assert len(chunks) == 1
        assert chunks[0].repo == "test"
        assert chunks[0].file_path == "test.cpp"
        assert chunks[0].confidence == 0.85
    
    async def test_score_chunks(self):
        """Test chunk relevance scoring."""
        from unittest.mock import Mock
        
        router = Mock()
        agent = ContextBuilderAgent(router)
        
        chunks = [
            CodeChunk(
                repo="test", file_path="test.cpp",
                start_line=100, end_line=120,
                content="void AppLauncher::Init() { segfault(); }",
                source="path_a", confidence=0.9,
            ),
        ]
        
        root_cause = "NULL pointer in AppLauncher::Init()"
        keywords = ["segfault", "null", "pointer"]
        
        scored = agent._score_chunks(chunks, root_cause, keywords)
        
        assert len(scored) == 1
        assert scored[0][1] > 0.5  # Should have decent score
    
    async def test_build_context_summary(self, sample_ticket, sample_root_cause):
        """Test context summary generation."""
        from unittest.mock import Mock
        
        router = Mock()
        agent = ContextBuilderAgent(router)
        
        chunks = [
            CodeChunk(
                repo="test", file_path="test.cpp",
                start_line=100, end_line=120,
                content="void func() {}",
                source="path_a", confidence=0.9,
            ),
        ]
        
        summary = agent._build_context_summary(
            ticket=sample_ticket,
            root_cause_result=sample_root_cause,
            keywords=["segfault"],
            code_keywords=[("malloc", 0.8)],
            chunks=chunks,
            similar_fixes=[],
            known_mistakes=[],
            device_context=None,
        )
        
        assert "TVPF-12345" in summary
        assert "NULL pointer" in summary
        assert "test.cpp" in summary


# ============================================================================
# INTEGRATION TEST
# ============================================================================

@pytest.mark.asyncio
class TestIntegration:
    """Integration tests for Context Builder."""
    
    async def test_end_to_end_context_build(self, sample_ticket, sample_root_cause):
        """Test end-to-end context building."""
        from unittest.mock import Mock, AsyncMock
        
        # Mock retrieval router
        router = Mock()
        
        # Create agent
        agent = ContextBuilderAgent(router, max_chunks=5)
        
        # Create mock RepoLocatorResult
        repo_result = RepoLocatorResult(
            primary_locations=[
                CodeLocation(
                    repo="vizio/smartcast-loki",
                    path="src/app/AppLauncher.cpp",
                    line_number=142,
                    confidence=0.9,
                    source="path_a",
                    content_preview="void AppLauncher::Init() {\n  if (ptr == nullptr) {\n    segfault();\n  }\n}",
                ),
            ],
            secondary_locations=[],
            similar_fixes=[],
            known_mistakes=[],
            confidence_score=0.85,
        )
        
        # Create mock PipelineState
        state = PipelineState(
            ticket=sample_ticket,
            quality_result=QualityResult(
                passed=True,
                score=0.95,
                total_lines=100,
                log_file_count=1,
                timestamp_coverage=0.90,
            ),
            buglayer_result=BugLayerResult(
                layer=BugLayer.LOKI,
                confidence=0.9,
                layer_scores={},
                matched_patterns=[],
            ),
        )
        
        # Build context
        context_result = await agent.build_context(
            state=state,
            repo_locator_result=repo_result,
            root_cause_result=sample_root_cause,
        )
        
        # Verify result
        assert context_result is not None
        assert len(context_result.context_summary) > 0
        assert sample_ticket.key in context_result.context_summary
        assert "AppLauncher" in context_result.context_summary


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
