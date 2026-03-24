"""
SAFS v6.0 — Fix Indexer

High-level write path: converts FixCandidate + PipelineState into FixRecord,
generates Voyage AI embeddings, and stores in Qdrant institutional memory.

Called asynchronously after a PR is successfully created (Phase 14 async).

Usage:
    indexer = FixIndexer(
        qdrant_url="http://localhost:6333",
        voyage_api_key=config.voyage_api_key,
    )
    fix_id = await indexer.index_fix(
        candidate=best_candidate,
        state=pipeline_state,
        pr_url="https://github.com/org/repo/pull/123",
    )
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from datetime import datetime, timezone
from math import log
from typing import Optional

from safs.log_analysis.models import (
    BugLayer,
    FixCandidate,
    FixStrategy,
    PipelineState,
)
from safs.qdrant_collections.institutional_memory import InstitutionalMemory
from safs.qdrant_collections.models import CorrectionRecord, FixRecord

logger = logging.getLogger(__name__)


class FixIndexer:
    """
    Indexes successful fixes into Qdrant institutional memory.

    Responsibilities:
    1. Build FixRecord from FixCandidate + PipelineState
    2. Generate dense embedding via Voyage AI (or compute heuristic fallback)
    3. Generate sparse BM25 vector from fix description
    4. Upsert into historical_fixes Qdrant collection
    """

    def __init__(
        self,
        qdrant_url: str = "http://localhost:6333",
        qdrant_api_key: Optional[str] = None,
        voyage_api_key: Optional[str] = None,
        voyage_model: str = "voyage-code-3",
        vector_dim: int = 1024,
    ) -> None:
        """
        Args:
            qdrant_url: Qdrant server URL
            qdrant_api_key: Optional Qdrant Cloud API key
            voyage_api_key: Voyage AI API key (for dense embeddings)
            voyage_model: Voyage embedding model name
            vector_dim: Embedding dimension (default 1024 for voyage-code-3)
        """
        self._memory = InstitutionalMemory(
            qdrant_url=qdrant_url,
            api_key=qdrant_api_key,
        )
        self._voyage_api_key = voyage_api_key
        self._voyage_model = voyage_model
        self._vector_dim = vector_dim

    async def index_fix(
        self,
        candidate: FixCandidate,
        state: PipelineState,
        pr_url: str,
        commit_sha: str = "",
    ) -> str:
        """
        Index a successful fix into institutional memory.

        Args:
            candidate: The winning FixCandidate
            state: Pipeline state with ticket, root cause, etc.
            pr_url: URL of created PR
            commit_sha: Git commit SHA (empty if not yet merged)

        Returns:
            fix_id of indexed record
        """
        # Build the fix record
        fix_record = self._build_fix_record(candidate, state, pr_url, commit_sha)

        # Compute description for embedding
        description = self._build_description(fix_record)

        # Generate embeddings
        dense_vector = await self._embed(description)
        sparse_vector = self._bm25_vector(description)

        # Store in Qdrant
        fix_id = await self._memory.add_fix(fix_record, dense_vector, sparse_vector)
        logger.info("Indexed fix %s for ticket %s → %s", fix_id, state.ticket.key, pr_url)
        return fix_id

    async def index_correction(
        self,
        original_fix_id: str,
        state: PipelineState,
        mistake_type: str,
        what_went_wrong: str,
        correct_approach: str,
        incorrect_code: Optional[str] = None,
        correct_code: Optional[str] = None,
    ) -> str:
        """
        Index a correction/regression (self-healing feedback loop).

        Called when a developer reports that an auto-generated fix was incorrect
        or caused a regression.

        Args:
            original_fix_id: ID of the original incorrect fix
            state: Pipeline state from the original pipeline run
            mistake_type: REGRESSION | INCOMPLETE_FIX | LOGIC_ERROR | WRONG_LAYER
            what_went_wrong: Human-readable explanation of the failure
            correct_approach: How it should have been fixed
            incorrect_code: The problematic code snippet
            correct_code: The correct code snippet

        Returns:
            correction_id of indexed record
        """
        correction = CorrectionRecord(
            correction_id=str(uuid.uuid4()),
            original_fix_id=original_fix_id,
            jira_ticket=state.ticket.key,
            error_category=state.root_cause_result.error_category.value
            if state.root_cause_result
            else "UNKNOWN",
            mistake_type=mistake_type,
            description=f"{mistake_type}: {what_went_wrong[:200]}",
            what_went_wrong=what_went_wrong,
            correct_approach=correct_approach,
            incorrect_code=incorrect_code,
            correct_code=correct_code,
            severity="HIGH",
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        description = f"{mistake_type} in {correction.error_category}: {what_went_wrong}"
        dense_vector = await self._embed(description)
        sparse_vector = self._bm25_vector(description)

        correction_id = await self._memory.add_correction(
            correction, dense_vector, sparse_vector
        )
        logger.info("Indexed correction %s for fix %s", correction_id, original_fix_id)
        return correction_id

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_fix_record(
        candidate: FixCandidate,
        state: PipelineState,
        pr_url: str,
        commit_sha: str,
    ) -> FixRecord:
        """Build FixRecord from pipeline artifacts."""
        root_cause = state.root_cause_result
        bug_layer = state.buglayer_result.layer if state.buglayer_result else BugLayer.UNKNOWN
        error_category = root_cause.error_category.value if root_cause else "UNKNOWN"

        files_changed = [
            fc.get("path", "unknown") for fc in candidate.file_changes
        ]

        return FixRecord(
            fix_id=str(uuid.uuid4()),
            jira_ticket=state.ticket.key,
            pr_url=pr_url,
            commit_sha=commit_sha,
            bug_layer=bug_layer.value,
            error_category=error_category,
            description=f"{state.ticket.summary}: {root_cause.root_cause[:300] if root_cause else ''}",
            root_cause=root_cause.root_cause if root_cause else "",
            fix_strategy=candidate.strategy.value,
            files_changed=files_changed,
            diff=candidate.diff,
            lines_added=candidate.diff.count("\n+") - 1 if candidate.diff else 0,
            lines_removed=candidate.diff.count("\n-") - 1 if candidate.diff else 0,
            created_at=datetime.now(timezone.utc).isoformat(),
            validation_success=candidate.validation_passed or False,
            confidence_score=candidate.ensemble_confidence or candidate.confidence,
            validation_method=_infer_validation_method(candidate),
            tags=[bug_layer.value.lower(), error_category.lower()],
            related_tickets=[state.ticket.key],
        )

    @staticmethod
    def _build_description(fix: FixRecord) -> str:
        """Build searchable description text for embedding."""
        return (
            f"Bug: {fix.description} "
            f"Layer: {fix.bug_layer} "
            f"Category: {fix.error_category} "
            f"Strategy: {fix.fix_strategy} "
            f"Files: {' '.join(fix.files_changed[:5])} "
            f"Root cause: {fix.root_cause[:200]}"
        )

    async def _embed(self, text: str) -> list[float]:
        """Generate dense embedding via Voyage AI."""
        if not self._voyage_api_key:
            return self._heuristic_vector(text)

        try:
            import httpx

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    "https://api.voyageai.com/v1/embeddings",
                    headers={
                        "Authorization": f"Bearer {self._voyage_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self._voyage_model,
                        "input": [text[:8000]],  # Voyage token limit
                        "input_type": "document",
                    },
                )
                response.raise_for_status()
                data = response.json()
                return data["data"][0]["embedding"]
        except Exception as exc:
            logger.warning("Voyage AI embedding failed: %s — using heuristic", exc)
            return self._heuristic_vector(text)

    def _heuristic_vector(self, text: str) -> list[float]:
        """
        Content-addressed deterministic heuristic vector fallback.

        Produces a 1024-dim float vector from the text hash. This is NOT
        semantically meaningful but allows the system to function without
        a Voyage AI key (e.g., in CI/CD or unit tests).
        """
        # Use SHA-256 of text to seed a deterministic sequence
        h = hashlib.sha256(text.encode()).digest()  # 32 bytes
        # Repeat hash bytes to fill 1024 floats
        seed_bytes = (h * (self._vector_dim // 32 + 1))[: self._vector_dim]
        # Normalize to [-1, 1]
        vector = [(b / 127.5) - 1.0 for b in seed_bytes]
        # L2-normalize
        norm = sum(v * v for v in vector) ** 0.5 or 1.0
        return [v / norm for v in vector]

    @staticmethod
    def _bm25_vector(text: str) -> dict:
        """
        Generate a simple BM25-approximation sparse vector.

        Returns:
            {indices: [int, ...], values: [float, ...]}

        The indices are hash-based token IDs (modulo 65536 to keep vocab bounded).
        """
        # Tokenize
        tokens = re.findall(r"\b\w{2,}\b", text.lower())
        if not tokens:
            return {"indices": [], "values": []}

        # Count term frequencies
        tf: dict[str, int] = {}
        for token in tokens:
            tf[token] = tf.get(token, 0) + 1

        total = len(tokens)
        # Compute TF-IDF-like weight (BM25 approximation without IDF)
        k1, b = 1.5, 0.75
        avg_dl = 10  # Assume average doc length
        dl = total

        indices = []
        values = []
        for token, count in tf.items():
            # Hash token to vocabulary index (0..65535)
            idx = int(hashlib.md5(token.encode(), usedforsecurity=False).hexdigest(), 16) % 65536
            # BM25 TF weight
            tf_weight = (count * (k1 + 1)) / (count + k1 * (1 - b + b * dl / avg_dl))
            indices.append(idx)
            values.append(tf_weight)

        return {"indices": indices, "values": values}


def _infer_validation_method(candidate: FixCandidate) -> str:
    """Infer validation method string from candidate results."""
    if candidate.validation_result:
        methods = []
        vr = candidate.validation_result
        if vr.path_alpha_qemu:
            methods.append("QEMU")
        if vr.path_beta_playwright:
            methods.append("PLAYWRIGHT")
        if vr.path_gamma_ondevice:
            methods.append("ON_DEVICE")
        if methods:
            return "+".join(methods)
    return "NONE"
