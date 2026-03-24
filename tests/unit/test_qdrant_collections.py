"""
Unit tests for Qdrant Collections (Phase 4)

Tests models, RRF fusion, temporal decay, collection setup, and institutional memory.
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock, patch

import pytest

from safs.qdrant_collections import (
    CorrectionRecord,
    FixRecord,
    InstitutionalMemory,
    QdrantCollectionManager, RRFFusion,
    RRFFusionConfig,
    SearchQuery,
    TemporalDecayConfig,
    TemporalDecayRanker,
)


# ==============================================================================
# FIXTURES
# ==============================================================================


@pytest.fixture
def fix_record():
    """Create sample FixRecord."""
    return FixRecord(
        fix_id=str(uuid.uuid4()),
        jira_ticket="TVPF-12345",
        pr_url="https://github.com/vizio/smartcast/pull/123",
        commit_sha="abc123def456",
        bug_layer="LOKI",
        error_category="LOKI_SE GFAULT_NULL_DEREF",
        description="Fixed null pointer dereference in AppLauncher",
        root_cause="Null check missing before dereferencing pointer",
        fix_strategy="NULL_CHECK",
        files_changed=["src/AppLauncher.cpp"],
        diff="+ if (ptr == nullptr) return;",
        lines_added=3,
        lines_removed=0,
        created_at=datetime.now(timezone.utc).isoformat(),
        validated_at=datetime.now(timezone.utc).isoformat(),
        validation_success=True,
        confidence_score=0.92,
        validation_method="QEMU",
        regression_detected=False,
        tags=["null-check", "c++"],
        related_tickets=["TVPF-12344"],
    )


@pytest.fixture
def correction_record():
    """Create sample CorrectionRecord."""
    return CorrectionRecord(
        correction_id=str(uuid.uuid4()),
        original_fix_id=str(uuid.uuid4()),
        jira_ticket="TVPF-12346",
        error_category="COMPANION_LIB_TIMING",
        mistake_type="INCOMPLETE_FIX",
        description="Original fix missed async race condition",
        what_went_wrong="Only added guard for initial load, not for reload",
        correct_approach="Guard both initial load and reload paths",
        incorrect_code="if (window.VIZIO) { ... }",
        correct_code="if (loaded && window.VIZIO) { ... }",
        created_at=datetime.now(timezone.utc).isoformat(),
        detected_by="TELEMETRY",
        severity="HIGH",
        lesson_learned="Always check all code paths",
        prevention_checklist=["Check all code paths", "Add integration test"],
        time_to_detect_hours=12.5,
        impacted_tickets=["TVPF-12350"],
    )


@pytest.fixture
def rrf_config():
    """Create RRF configuration."""
    return RRFFusionConfig(
        k=60,
        sparse_weight=0.5,
        dense_weight=0.5,
        prefetch_limit_multiplier=3,
    )


@pytest.fixture
def decay_config():
    """Create temporal decay configuration."""
    return TemporalDecayConfig()


# ==============================================================================
# MODEL TESTS
# ==============================================================================


class TestModels:
    """Test Pydantic models."""
    
    def test_fix_record_creation(self, fix_record):
        """Test FixRecord creation and validation."""
        assert fix_record.jira_ticket == "TVPF-12345"
        assert fix_record.bug_layer == "LOKI"
        assert fix_record.confidence_score == 0.92
        assert len(fix_record.files_changed) == 1
    
    def test_fix_record_serialization(self, fix_record):
        """Test FixRecord JSON serialization."""
        data = fix_record.model_dump()
        assert data["jira_ticket"] == "TVPF-12345"
        assert data["bug_layer"] == "LOKI"
        
        # Deserialize
        restored = FixRecord(**data)
        assert restored.fix_id == fix_record.fix_id
    
    def test_correction_record_creation(self, correction_record):
        """Test CorrectionRecord creation and validation."""
        assert correction_record.jira_ticket == "TVPF-12346"
        assert correction_record.mistake_type == "INCOMPLETE_FIX"
        assert correction_record.severity == "HIGH"
        assert correction_record.time_to_detect_hours == 12.5
    
    def test_correction_record_serialization(self, correction_record):
        """Test CorrectionRecord JSON serialization."""
        data = correction_record.model_dump()
        assert data["mistake_type"] == "INCOMPLETE_FIX"
        
        # Deserialize
        restored = CorrectionRecord(**data)
        assert restored.correction_id == correction_record.correction_id
    
    def test_search_query_defaults(self):
        """Test SearchQuery with defaults."""
        query = SearchQuery(text="segmentation fault")
        assert query.top_k == 5
        assert query.exclude_regressions is True
        assert query.bug_layer is None
    
    def test_search_query_with_filters(self):
        """Test SearchQuery with all filters."""
        query = SearchQuery(
            text="null pointer",
            bug_layer="LOKI",
            error_category="LOKI_SEGFAULT_NULL_DEREF",
            top_k=10,
            min_confidence=0.8,
            validation_method="QEMU",
            exclude_regressions=False,
            max_age_days=365,
        )
        assert query.top_k == 10
        assert query.bug_layer == "LOKI"
        assert query.min_confidence == 0.8
    
    def test_rrf_config_validation(self):
        """Test RRF config validation."""
        config = RRFFusionConfig(k=60, sparse_weight=0.5, dense_weight=0.5)
        assert config.k == 60
        assert config.sparse_weight + config.dense_weight == 1.0
    
    def test_temporal_decay_config(self, decay_config):
        """Test temporal decay configuration."""
        assert decay_config.get_halflife("COMPANION_LIB_TIMING") == 90
        assert decay_config.get_halflife("UNKNOWN_CATEGORY") == 365  # DEFAULT


# ==============================================================================
# RRF FUSION TESTS
# ==============================================================================


class TestRRFFusion:
    """Test Reciprocal Rank Fusion algorithm."""
    
    def test_rrf_basic_fusion(self, rrf_config):
        """Test basic RRF fusion of sparse + dense results."""
        rrf = RRFFusion(rrf_config)
        
        # Sparse results (BM25)
        sparse_results = [
            {"id": "doc1", "score": 0.95, "payload": {"text": "doc1"}},
            {"id": "doc2", "score": 0.85, "payload": {"text": "doc2"}},
            {"id": "doc3", "score": 0.75, "payload": {"text": "doc3"}},
        ]
        
        # Dense results (voyage-code-3)
        dense_results = [
            {"id": "doc2", "score": 0.90, "payload": {"text": "doc2"}},
            {"id": "doc1", "score": 0.88, "payload": {"text": "doc1"}},
            {"id": "doc4", "score": 0.80, "payload": {"text": "doc4"}},
        ]
        
        fused = rrf.fuse(sparse_results, dense_results)
        
        # Check fusion worked
        assert len(fused) == 4  # doc1, doc2, doc3, doc4
        assert all("score" in r for r in fused)
        assert all("sparse_score" in r or "dense_score" in r for r in fused)
        
        # Results should be sorted by RRF score
        scores = [r["score"] for r in fused]
        assert scores == sorted(scores, reverse=True)
    
    def test_rrf_both_rankings(self, rrf_config):
        """Test doc appearing in both rankings gets highest score."""
        rrf = RRFFusion(rrf_config)
        
        sparse_results = [
            {"id": "doc1", "score": 0.95, "payload": {"text": "doc1"}},
            {"id": "doc2", "score": 0.85, "payload": {"text": "doc2"}},
        ]
        
        dense_results = [
            {"id": "doc1", "score": 0.90, "payload": {"text": "doc1"}},
            {"id": "doc3", "score": 0.80, "payload": {"text": "doc3"}},
        ]
        
        fused = rrf.fuse(sparse_results, dense_results)
        
        # doc1 appears in both, should be ranked highest
        assert fused[0]["id"] == "doc1"
        assert fused[0]["sparse_score"] is not None
        assert fused[0]["dense_score"] is not None
    
    def test_rrf_sparse_only(self, rrf_config):
        """Test doc appearing only in sparse ranking."""
        rrf = RRFFusion(rrf_config)
        
        sparse_results = [
            {"id": "doc1", "score": 0.95, "payload": {"text": "doc1"}},
        ]
        
        dense_results = [
            {"id": "doc2", "score": 0.90, "payload": {"text": "doc2"}},
        ]
        
        fused = rrf.fuse(sparse_results, dense_results)
        
        # Find doc1
        doc1 = next(r for r in fused if r["id"] == "doc1")
        assert doc1["sparse_score"] is not None
        assert doc1["dense_score"] is None
    
    def test_rrf_dense_only(self, rrf_config):
        """Test doc appearing only in dense ranking."""
        rrf = RRFFusion(rrf_config)
        
        sparse_results = [
            {"id": "doc1", "score": 0.95, "payload": {"text": "doc1"}},
        ]
        
        dense_results = [
            {"id": "doc2", "score": 0.90, "payload": {"text": "doc2"}},
        ]
        
        fused = rrf.fuse(sparse_results, dense_results)
        
        # Find doc2
        doc2 = next(r for r in fused if r["id"] == "doc2")
        assert doc2["sparse_score"] is None
        assert doc2["dense_score"] is not None
    
    def test_rrf_empty_sparse(self, rrf_config):
        """Test RRF with empty sparse results."""
        rrf = RRFFusion(rrf_config)
        
        sparse_results = []
        dense_results = [
            {"id": "doc1", "score": 0.90, "payload": {"text": "doc1"}},
        ]
        
        fused = rrf.fuse(sparse_results, dense_results)
        
        assert len(fused) == 1
        assert fused[0]["id"] == "doc1"
    
    def test_rrf_empty_dense(self, rrf_config):
        """Test RRF with empty dense results."""
        rrf = RRFFusion(rrf_config)
        
        sparse_results = [
            {"id": "doc1", "score": 0.95, "payload": {"text": "doc1"}},
        ]
        dense_results = []
        
        fused = rrf.fuse(sparse_results, dense_results)
        
        assert len(fused) == 1
        assert fused[0]["id"] == "doc1"
    
    def test_rrf_weighted_fusion(self):
        """Test RRF with different weights."""
        config = RRFFusionConfig(k=60, sparse_weight=0.7, dense_weight=0.3)
        rrf = RRFFusion(config)
        
        sparse_results = [{"id": "doc1", "score": 0.95, "payload": {}}]
        dense_results = [{"id": "doc1", "score": 0.90, "payload": {}}]
        
        fused = rrf.fuse(sparse_results, dense_results)
        
        # Sparse weighted higher, should contribute more
        assert fused[0]["sparse_score"] * 0.7 > fused[0]["dense_score"] * 0.3


# ==============================================================================
# TEMPORAL DECAY TESTS
# ==============================================================================


class TestTemporalDecay:
    """Test temporal decay re-ranking."""
    
    def test_decay_recent_fix(self, decay_config):
        """Test decay weight for recent fix (1 day old)."""
        ranker = TemporalDecayRanker(decay_config)
        
        created_at = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        weight = ranker.calculate_decay_weight(created_at, "COMPANION_LIB_TIMING")
        
        # Recent fix should have high weight (close to 1.0)
        assert weight > 0.98
    
    def test_decay_old_fix(self, decay_config):
        """Test decay weight for old fix (1 year old)."""
        ranker = TemporalDecayRanker(decay_config)
        
        created_at = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
        weight = ranker.calculate_decay_weight(created_at, "COMPANION_LIB_TIMING")
        
        # 1 year old with 90-day half-life should be significantly decayed
        assert weight < 0.5
    
    def test_decay_category_specific_halflife(self, decay_config):
        """Test different decay rates for different categories."""
        ranker = TemporalDecayRanker(decay_config)
        
        # Same age, different categories
        created_at = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
        
        weight_companion = ranker.calculate_decay_weight(created_at, "COMPANION_LIB_TIMING")  # 90-day half-life
        weight_loki = ranker.calculate_decay_weight(created_at, "LOKI_SEGFAULT_NULL_DEREF")  # 730-day half-life
        
        # Companion should decay faster (lower weight)
        assert weight_companion < weight_loki
    
    def test_decay_unknown_category(self, decay_config):
        """Test decay for unknown category (uses DEFAULT)."""
        ranker = TemporalDecayRanker(decay_config)
        
        created_at = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
        weight = ranker.calculate_decay_weight(created_at, "UNKNOWN_CATEGORY")
        
        # Should use DEFAULT half-life (365 days)
        # At 365 days with 365-day half-life: 1 / (1 + 1) = 0.5
        assert 0.45 <= weight <= 0.55
    
    def test_rerank_applies_decay(self, decay_config):
        """Test re-ranking applies temporal decay."""
        ranker = TemporalDecayRanker(decay_config)
        
        recent_dt = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        old_dt = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
        
        results = [
            {"id": "old", "score": 0.95, "payload": {"created_at": old_dt}},
            {"id": "recent", "score": 0.90, "payload": {"created_at": recent_dt}},
        ]
        
        reranked = ranker.rerank(results, "COMPANION_LIB_TIMING")
        
        # Recent result should be ranked higher after decay
        assert reranked[0].record["created_at"] == recent_dt
        assert reranked[0].temporal_score > reranked[1].temporal_score
    
    def test_rerank_no_timestamp(self, decay_config):
        """Test re-ranking with missing timestamp."""
        ranker = TemporalDecayRanker(decay_config)
        
        results = [
            {"id": "doc1", "score": 0.95, "payload": {}},  # No created_at
        ]
        
        reranked = ranker.rerank(results, "DEFAULT")
        
        # Should keep original score (decay weight = 1.0)
        assert reranked[0].decay_weight == 1.0
        assert reranked[0].temporal_score == 0.95
    
    def test_rerank_preserves_metadata(self, decay_config):
        """Test re-ranking preserves sparse/dense scores."""
        ranker = TemporalDecayRanker(decay_config)
        
        recent_dt = datetime.now(timezone.utc).isoformat()
        
        results = [
            {
                "id": "doc1",
                "score": 0.95,
                "sparse_score": 0.90,
                "dense_score": 0.85,
                "payload": {"created_at": recent_dt},
            },
        ]
        
        reranked = ranker.rerank(results, "DEFAULT")
        
        assert reranked[0].sparse_score == 0.90
        assert reranked[0].dense_score == 0.85
        assert reranked[0].age_days is not None


# ==============================================================================
# COLLECTION SETUP TESTS
# ==============================================================================


class TestCollectionSetup:
    """Test Qdrant collection management."""
    
    @patch("safs.qdrant_collections.collection_setup.QdrantClient")
    def test_collection_manager_init(self, mock_client_class):
        """Test collection manager initialization."""
        manager = QdrantCollectionManager(qdrant_url="http://localhost:6333")
        
        mock_client_class.assert_called_once_with(
            url="http://localhost:6333",
            api_key=None,
        )
    
    @patch("safs.qdrant_collections.collection_setup.QdrantClient")
    def test_create_collection(self, mock_client_class):
        """Test creating a single collection."""
        mock_client = Mock()
        mock_client.get_collections.return_value = Mock(collections=[])
        mock_client_class.return_value = mock_client
        
        manager = QdrantCollectionManager()
        manager.create_collection("historical_fixes")
        
        mock_client.create_collection.assert_called_once()
        call_args = mock_client.create_collection.call_args
        assert call_args[1]["collection_name"] == "historical_fixes"
    
    @patch("safs.qdrant_collections.collection_setup.QdrantClient")
    def test_create_collections_both(self, mock_client_class):
        """Test creating both collections."""
        mock_client = Mock()
        mock_client.get_collections.return_value = Mock(collections=[])
        mock_client_class.return_value = mock_client
        
        manager = QdrantCollectionManager()
        manager.create_collections()
        
        # Should create both collections
        assert mock_client.create_collection.call_count == 2
    
    @patch("safs.qdrant_collections.collection_setup.QdrantClient")
    def test_collection_exists(self, mock_client_class):
        """Test checking collection existence."""
        mock_client = Mock()
        mock_collection = Mock()
        mock_collection.name = "historical_fixes"
        mock_client.get_collections.return_value = Mock(collections=[mock_collection])
        mock_client_class.return_value = mock_client
        
        manager = QdrantCollectionManager()
        
        assert manager.collection_exists("historical_fixes") is True
        assert manager.collection_exists("nonexistent") is False
    
    @patch("safs.qdrant_collections.collection_setup.QdrantClient")
    def test_delete_collection(self, mock_client_class):
        """Test deleting a collection."""
        mock_client = Mock()
        mock_collection = Mock()
        mock_collection.name = "historical_fixes"
        mock_client.get_collections.return_value = Mock(collections=[mock_collection])
        mock_client_class.return_value = mock_client
        
        manager = QdrantCollectionManager()
        manager.delete_collection("historical_fixes")
        
        mock_client.delete_collection.assert_called_once_with("historical_fixes")
    
    @patch("safs.qdrant_collections.collection_setup.QdrantClient")
    def test_get_collection_info(self, mock_client_class):
        """Test getting collection info."""
        mock_client = Mock()
        mock_collection = Mock()
        mock_collection.name = "historical_fixes"
        mock_client.get_collections.return_value = Mock(collections=[mock_collection])
        
        mock_info = Mock()
        mock_info.vectors_count = 1024
        mock_info.points_count = 100
        mock_info.segments_count = 1
        mock_info.status = "green"
        mock_client.get_collection.return_value = mock_info
        
        mock_client_class.return_value = mock_client
        
        manager = QdrantCollectionManager()
        info = manager.get_collection_info("historical_fixes")
        
        assert info["exists"] is True
        assert info["points_count"] == 100
    
    @patch("safs.qdrant_collections.collection_setup.QdrantClient")
    def test_setup_all(self, mock_client_class):
        """Test setting up all collections."""
        mock_client = Mock()
        mock_client.get_collections.return_value = Mock(collections=[])
        mock_client_class.return_value = mock_client
        
        manager = QdrantCollectionManager()
        status = manager.setup_all(recreate=False)
        
        assert "historical_fixes" in status
        assert "fix_corrections" in status


# ==============================================================================
# INSTITUTIONAL MEMORY TESTS (Mocked Qdrant)
# ==============================================================================


class TestInstitutionalMemory:
    """Test InstitutionalMemory with mocked Qdrant."""
    
    @patch("safs.qdrant_collections.institutional_memory.QdrantClient")
    async def test_add_fix(self, mock_client_class, fix_record):
        """Test adding a fix record."""
        mock_client = Mock()
        mock_client_class.return_value = mock_client
        
        memory = InstitutionalMemory()
        
        dense_vector = [0.1] * 1024
        sparse_vector = {"indices": [1, 2, 3], "values": [0.5, 0.3, 0.2]}
        
        fix_id = await memory.add_fix(fix_record, dense_vector, sparse_vector)
        
        assert fix_id == fix_record.fix_id
        mock_client.upsert.assert_called_once()
    
    @patch("safs.qdrant_collections.institutional_memory.QdrantClient")
    async def test_add_correction(self, mock_client_class, correction_record):
        """Test adding a correction record."""
        mock_client = Mock()
        mock_client_class.return_value = mock_client
        
        memory = InstitutionalMemory()
        
        dense_vector = [0.1] * 1024
        sparse_vector = {"indices": [1, 2, 3], "values": [0.5, 0.3, 0.2]}
        
        correction_id = await memory.add_correction(correction_record, dense_vector, sparse_vector)
        
        assert correction_id == correction_record.correction_id
        mock_client.upsert.assert_called_once()
    
    @patch("safs.qdrant_collections.institutional_memory.QdrantClient")
    async def test_find_similar_fixes(self, mock_client_class):
        """Test finding similar fixes with hybrid retrieval."""
        mock_client = Mock()
        
        # Mock search results
        mock_result = Mock()
        mock_result.id = "fix1"
        mock_result.score = 0.95
        mock_result.payload = {
            "fix_id": "fix1",
            "description": "Test fix",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "error_category": "LOKI_SEGFAULT_NULL_DEREF",
        }
        
        mock_client.search.return_value = [mock_result]
        mock_client_class.return_value = mock_client
        
        memory = InstitutionalMemory()
        
        query = SearchQuery(
            text="segmentation fault",
            bug_layer="LOKI",
            error_category="LOKI_SEGFAULT_NULL_DEREF",
            top_k=5,
        )
        
        dense_vector = [0.1] * 1024
        sparse_vector = {"indices": [1, 2, 3], "values": [0.5, 0.3, 0.2]}
        
        results = await memory.find_similar_fixes(query, dense_vector, sparse_vector)
        
        # Should call search twice (sparse + dense)
        assert mock_client.search.call_count == 2
        
        # Should return SearchResult objects
        assert len(results) > 0
        assert results[0].record["fix_id"] == "fix1"
    
    @patch("safs.qdrant_collections.institutional_memory.QdrantClient")
    async def test_find_known_mistakes(self, mock_client_class):
        """Test finding known mistakes."""
        mock_client = Mock()
        
        # Mock search results
        mock_result = Mock()
        mock_result.id = "correction1"
        mock_result.score = 0.90
        mock_result.payload = {
            "correction_id": "correction1",
            "description": "Test mistake",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "error_category": "COMPANION_LIB_TIMING",
        }
        
        mock_client.search.return_value = [mock_result]
        mock_client_class.return_value = mock_client
        
        memory = InstitutionalMemory()
        
        query = SearchQuery(
            text="companion library",
            error_category="COMPANION_LIB_TIMING",
            top_k=3,
        )
        
        dense_vector = [0.1] * 1024
        sparse_vector = {"indices": [1, 2, 3], "values": [0.5, 0.3, 0.2]}
        
        results = await memory.find_known_mistakes(query, dense_vector, sparse_vector)
        
        # Should call search twice (sparse + dense)
        assert mock_client.search.call_count == 2
        
        # Should return SearchResult objects
        assert len(results) > 0
        assert results[0].record["correction_id"] == "correction1"


# ==============================================================================
# INTEGRATION TESTS
# ==============================================================================


class TestIntegration:
    """Integration tests (require running Qdrant)."""
    
    @pytest.mark.skip(reason="Requires running Qdrant instance")
    def test_end_to_end_flow(self):
        """Test complete flow: setup → add → search → temporal decay."""
        # Setup collections
        manager = QdrantCollectionManager()
        manager.create_collections(recreate=True)
        
        # Add fix
        memory = InstitutionalMemory()
        fix = FixRecord(
            fix_id=str(uuid.uuid4()),
            jira_ticket="TVPF-12345",
            pr_url="https://github.com/test/pull/1",
            commit_sha="abc123",
            bug_layer="LOKI",
            error_category="LOKI_SEGFAULT_NULL_DEREF",
            description="Fixed segfault",
            root_cause="Null pointer",
            fix_strategy="NULL_CHECK",
            files_changed=["test.cpp"],
            diff="+ if (ptr) ...",
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        
        # Mock vectors (would come from voyage-code-3 in reality)
        dense_vector = [0.1] * 1024
        sparse_vector = {"indices": [1, 2, 3], "values": [0.5, 0.3, 0.2]}
        
        # Add and search
        # await memory.add_fix(fix, dense_vector, sparse_vector)
        #
        # query = SearchQuery(text="segfault", bug_layer="LOKI", top_k=5)
        # results = await memory.find_similar_fixes(query, dense_vector, sparse_vector)
        #
        # assert len(results) > 0
