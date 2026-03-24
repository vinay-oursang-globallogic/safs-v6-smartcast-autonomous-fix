"""
SAFS v6.0 — Institutional Memory with RRF Fusion + Temporal Decay

Implements retrieval from Qdrant collections with:
- RRF (Reciprocal Rank Fusion) combining BM25 sparse + voyage-code-3 dense
- Temporal decay re-ranking with category-specific half-lives
- Filter by bug_layer, error_category, validation status, etc.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Filter,
    FieldCondition,
    MatchValue,
    PointStruct,
    Range,
)

from .models import (
    CorrectionRecord,
    FixRecord,
    RRFFusionConfig,
    SearchQuery,
    SearchResult,
    TemporalDecayConfig,
)

logger = logging.getLogger(__name__)


class RRFFusion:
    """
    Reciprocal Rank Fusion algorithm for combining sparse + dense retrieval.
    
    Formula: score(d) = sum over rankings r of 1/(k + rank_r(d))
    
    where k = constant (default 60), rank_r(d) = rank of document d in ranking r
    """
    
    def __init__(self, config: Optional[RRFFusionConfig] = None):
        """
        Initialize RRF fusion.
        
        Args:
            config: RRF configuration (uses defaults if None)
        """
        self.config = config or RRFFusionConfig()
    
    def fuse(self, sparse_results: List[dict], dense_results: List[dict]) -> List[dict]:
        """
        Fuse sparse and dense results using RRF.
        
        Args:
            sparse_results: Results from BM25 sparse retrieval
            dense_results: Results from voyage-code-3 dense retrieval
            
        Returns:
            Fused results sorted by RRF score (descending)
        """
        # Build rank maps: point_id -> rank
        sparse_ranks = {r["id"]: rank + 1 for rank, r in enumerate(sparse_results)}
        dense_ranks = {r["id"]: rank + 1 for rank, r in enumerate(dense_results)}
        
        # Collect all unique point IDs
        all_ids = set(sparse_ranks.keys()) | set(dense_ranks.keys())
        
        # Calculate RRF scores
        fused = []
        for point_id in all_ids:
            sparse_rank = sparse_ranks.get(point_id, float('inf'))
            dense_rank = dense_ranks.get(point_id, float('inf'))
            
            # RRF formula
            sparse_score = 0.0 if sparse_rank == float('inf') else 1.0 / (self.config.k + sparse_rank)
            dense_score = 0.0 if dense_rank == float('inf') else 1.0 / (self.config.k + dense_rank)
            
            # Weighted combination
            rrf_score = (
                self.config.sparse_weight * sparse_score +
                self.config.dense_weight * dense_score
            )
            
            # Find original result for payload
            result = None
            for r in sparse_results:
                if r["id"] == point_id:
                    result = r
                    break
            if result is None:
                for r in dense_results:
                    if r["id"] == point_id:
                        result = r
                        break
            
            if result:
                fused.append({
                    "id": point_id,
                    "score": rrf_score,
                    "sparse_score": sparse_score if sparse_rank != float('inf') else None,
                    "dense_score": dense_score if dense_rank != float('inf') else None,
                    "payload": result.get("payload", {}),
                })
        
        # Sort by RRF score (descending)
        fused.sort(key=lambda x: x["score"], reverse=True)
        
        return fused


class TemporalDecayRanker:
    """
    Temporal decay re-ranking with category-specific half-lives.
    
    Records NEVER deleted — only down-ranked by age.
    """
    
    def __init__(self, config: Optional[TemporalDecayConfig] = None):
        """
        Initialize temporal decay ranker.
        
        Args:
            config: Temporal decay configuration (uses defaults if None)
        """
        self.config = config or TemporalDecayConfig()
    
    def calculate_decay_weight(self, created_at: str, error_category: str) -> float:
        """
        Calculate temporal decay weight.
        
        Formula: weight = 1 / ((age_days / halflife) + 1)
        
        Args:
            created_at: ISO8601 timestamp
            error_category: Error category for half-life lookup
            
        Returns:
            Decay weight (0.0-1.0)
        """
        try:
            created_dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            now = datetime.now(timezone.utc)
            age_days = (now - created_dt).days
            
            halflife = self.config.get_halflife(error_category)
            weight = 1.0 / ((age_days / halflife) + 1.0)
            
            return weight
        except Exception as e:
            logger.warning(f"Error calculating decay weight: {e}")
            return 1.0  # No decay on error
    
    def rerank(self, results: List[dict], error_category: str) -> List[SearchResult]:
        """
        Re-rank results by applying temporal decay.
        
        Args:
            results: Fused RRF results
            error_category: Error category for half-life lookup
            
        Returns:
            Re-ranked results as SearchResult objects
        """
        scored = []
        
        for r in results:
            created_at = r["payload"].get("created_at")
            if not created_at:
                # No timestamp, keep original score
                scored.append(SearchResult(
                    score=r["score"],
                    sparse_score=r.get("sparse_score"),
                    dense_score=r.get("dense_score"),
                    temporal_score=r["score"],
                    age_days=None,
                    decay_weight=1.0,
                    record=r["payload"],
                ))
                continue
            
            # Calculate decay
            decay_weight = self.calculate_decay_weight(created_at, error_category)
            temporal_score = r["score"] * decay_weight
            
            # Calculate age in days
            try:
                created_dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                now = datetime.now(timezone.utc)
                age_days = (now - created_dt).days
            except Exception:
                age_days = None
            
            scored.append(SearchResult(
                score=r["score"],
                sparse_score=r.get("sparse_score"),
                dense_score=r.get("dense_score"),
                temporal_score=temporal_score,
                age_days=age_days,
                decay_weight=decay_weight,
                record=r["payload"],
            ))
        
        # Sort by temporal score (descending)
        scored.sort(key=lambda x: x.temporal_score or 0, reverse=True)
        
        return scored


class InstitutionalMemory:
    """
    Qdrant institutional memory management.
    
    Handles:
    - Inserting fix records and correction records
    - Hybrid retrieval with RRF fusion
    - Temporal decay re-ranking
    - Filtering by bug_layer, error_category, etc.
    """
    
    HISTORICAL_FIXES = "historical_fixes"
    FIX_CORRECTIONS = "fix_corrections"
    
    def __init__(
        self,
        qdrant_url: str = "http://localhost:6333",
        api_key: Optional[str] = None,
        rrf_config: Optional[RRFFusionConfig] = None,
        decay_config: Optional[TemporalDecayConfig] = None,
    ):
        """
        Initialize institutional memory.
        
        Args:
            qdrant_url: Qdrant server URL
            api_key: Optional API key for Qdrant Cloud
            rrf_config: RRF fusion configuration
            decay_config: Temporal decay configuration
        """
        self.client = QdrantClient(url=qdrant_url, api_key=api_key)
        self.rrf = RRFFusion(rrf_config)
        self.decay_ranker = TemporalDecayRanker(decay_config)
        logger.info(f"Initialized InstitutionalMemory with Qdrant at {qdrant_url}")
    
    async def add_fix(self, fix: FixRecord, dense_vector: List[float], sparse_vector: dict) -> str:
        """
        Add a fix record to historical_fixes collection.
        
        Args:
            fix: FixRecord to add
            dense_vector: voyage-code-3 embedding (1024-dim)
            sparse_vector: BM25 sparse vector {indices: [...], values: [...]}
            
        Returns:
            Point ID (fix_id)
        """
        point = PointStruct(
            id=fix.fix_id,
            vector={
                "dense": dense_vector,
                "sparse": sparse_vector,
            },
            payload=fix.model_dump(),
        )
        
        self.client.upsert(collection_name=self.HISTORICAL_FIXES, points=[point])
        logger.info(f"Added fix record: {fix.fix_id}")
        
        return fix.fix_id
    
    async def add_correction(self, correction: CorrectionRecord, dense_vector: List[float], sparse_vector: dict) -> str:
        """
        Add a correction record to fix_corrections collection.
        
        Args:
            correction: CorrectionRecord to add
            dense_vector: voyage-code-3 embedding (1024-dim)
            sparse_vector: BM25 sparse vector {indices: [...], values: [...]}
            
        Returns:
            Point ID (correction_id)
        """
        point = PointStruct(
            id=correction.correction_id,
            vector={
                "dense": dense_vector,
                "sparse": sparse_vector,
            },
            payload=correction.model_dump(),
        )
        
        self.client.upsert(collection_name=self.FIX_CORRECTIONS, points=[point])
        logger.info(f"Added correction record: {correction.correction_id}")
        
        return correction.correction_id
    
    def _build_filter(self, query: SearchQuery) -> Optional[Filter]:
        """
        Build Qdrant filter from SearchQuery.
        
        Args:
            query: Search query with filters
            
        Returns:
            Qdrant Filter object or None
        """
        conditions = []
        
        if query.bug_layer:
            conditions.append(FieldCondition(
                key="bug_layer",
                match=MatchValue(value=query.bug_layer),
            ))
        
        if query.error_category:
            conditions.append(FieldCondition(
                key="error_category",
                match=MatchValue(value=query.error_category),
            ))
        
        if query.validation_method:
            conditions.append(FieldCondition(
                key="validation_method",
                match=MatchValue(value=query.validation_method),
            ))
        
        if query.exclude_regressions:
            conditions.append(FieldCondition(
                key="regression_detected",
                match=MatchValue(value=False),
            ))
        
        if query.min_confidence is not None:
            conditions.append(FieldCondition(
                key="confidence_score",
                range=Range(gte=query.min_confidence),
            ))
        
        if query.max_age_days is not None:
            cutoff_date = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(days=query.max_age_days)
            conditions.append(FieldCondition(
                key="created_at",
                range=Range(gte=cutoff_date.isoformat()),
            ))
        
        return Filter(must=conditions) if conditions else None
    
    async def find_similar_fixes(self, query: SearchQuery, dense_vector: List[float], sparse_vector: dict) -> List[SearchResult]:
        """
        Find similar fixes using hybrid RRF retrieval + temporal decay.
        
        Args:
            query: Search query with filters
            dense_vector: Query embedding (voyage-code-3, 1024-dim)
            sparse_vector: Query sparse vector (BM25)
            
        Returns:
            List of SearchResult objects sorted by temporal score
        """
        filter_cond = self._build_filter(query)
        prefetch_limit = query.top_k * self.rrf.config.prefetch_limit_multiplier
        
        # Sparse retrieval
        sparse_results = self.client.search(
            collection_name=self.HISTORICAL_FIXES,
            query_vector=("sparse", sparse_vector),
            query_filter=filter_cond,
            limit=prefetch_limit,
            with_payload=True,
        )
        
        # Dense retrieval
        dense_results = self.client.search(
            collection_name=self.HISTORICAL_FIXES,
            query_vector=("dense", dense_vector),
            query_filter=filter_cond,
            limit=prefetch_limit,
            with_payload=True,
        )
        
        # Convert to dict format
        sparse_dicts = [{"id": r.id, "score": r.score, "payload": r.payload} for r in sparse_results]
        dense_dicts = [{"id": r.id, "score": r.score, "payload": r.payload} for r in dense_results]
        
        # RRF fusion
        fused = self.rrf.fuse(sparse_dicts, dense_dicts)
        
        # Temporal decay re-ranking
        error_category = query.error_category or "DEFAULT"
        reranked = self.decay_ranker.rerank(fused, error_category)
        
        return reranked[:query.top_k]
    
    async def find_known_mistakes(self, query: SearchQuery, dense_vector: List[float], sparse_vector: dict) -> List[SearchResult]:
        """
        Find known mistakes using hybrid RRF retrieval + temporal decay.
        
        Args:
            query: Search query with filters
            dense_vector: Query embedding (voyage-code-3, 1024-dim)
            sparse_vector: Query sparse vector (BM25)
            
        Returns:
            List of SearchResult objects sorted by temporal score
        """
        filter_cond = self._build_filter(query)
        prefetch_limit = query.top_k * self.rrf.config.prefetch_limit_multiplier
        
        # Sparse retrieval
        sparse_results = self.client.search(
            collection_name=self.FIX_CORRECTIONS,
            query_vector=("sparse", sparse_vector),
            query_filter=filter_cond,
            limit=prefetch_limit,
            with_payload=True,
        )
        
        # Dense retrieval
        dense_results = self.client.search(
            collection_name=self.FIX_CORRECTIONS,
            query_vector=("dense", dense_vector),
            query_filter=filter_cond,
            limit=prefetch_limit,
            with_payload=True,
        )
        
        # Convert to dict format
        sparse_dicts = [{"id": r.id, "score": r.score, "payload": r.payload} for r in sparse_results]
        dense_dicts = [{"id": r.id, "score": r.score, "payload": r.payload} for r in dense_results]
        
        # RRF fusion
        fused = self.rrf.fuse(sparse_dicts, dense_dicts)
        
        # Temporal decay re-ranking
        error_category = query.error_category or "DEFAULT"
        reranked = self.decay_ranker.rerank(fused, error_category)
        
        return reranked[:query.top_k]
