"""
SAFS v6.0 — Correction Indexer

Writes developer corrections, PR rejections, and regression records to the
``fix_corrections`` Qdrant collection so the pipeline learns from mistakes.

Called by :class:`~safs.agents.self_healing.SelfHealingAgent`.

Example usage::

    indexer = CorrectionIndexer(
        qdrant_url="http://localhost:6333",
        voyage_api_key=config.voyage_api_key,
    )
    correction_id = await indexer.index_correction(record)
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timezone
from math import log
from typing import Optional

from safs.qdrant_collections.institutional_memory import InstitutionalMemory
from safs.qdrant_collections.models import CorrectionRecord

logger = logging.getLogger(__name__)

# ─── Constants ─────────────────────────────────────────────────────────────────
_CORRECTIONS_COLLECTION = "fix_corrections"
_VECTOR_DIM = 1024


class CorrectionIndexer:
    """
    Indexes fix corrections (mistakes) into Qdrant ``fix_corrections``.

    Args:
        qdrant_url: Qdrant server URL.
        qdrant_api_key: Optional Qdrant Cloud API key.
        voyage_api_key: Voyage AI API key for dense embeddings.
        voyage_model: Voyage embedding model name.
    """

    def __init__(
        self,
        qdrant_url: str = "http://localhost:6333",
        qdrant_api_key: Optional[str] = None,
        voyage_api_key: Optional[str] = None,
        voyage_model: str = "voyage-code-3",
    ) -> None:
        self._memory = InstitutionalMemory(
            qdrant_url=qdrant_url,
            api_key=qdrant_api_key,
        )
        self._voyage_api_key = voyage_api_key
        self._voyage_model = voyage_model

    async def index_correction(self, record: CorrectionRecord) -> str:
        """
        Persist *record* to Qdrant and return the stored point ID.

        Args:
            record: :class:`~safs.qdrant_collections.models.CorrectionRecord`
                populated by the self-healing agent.

        Returns:
            UUID string of the created Qdrant point.
        """
        point_id = str(uuid.uuid4())

        text_for_embedding = (
            f"CORRECTION: {record.description}\n"
            f"MISTAKE: {record.what_went_wrong}\n"
            f"CORRECT APPROACH: {record.correct_approach}\n"
            f"LESSON: {record.lesson_learned}"
        )

        dense_vector = await self._embed(text_for_embedding)
        sparse_vector = self._bm25_sparse(text_for_embedding)

        payload = {
            "correction_id": record.correction_id,
            "original_fix_id": record.original_fix_id,
            "jira_ticket": record.jira_ticket,
            "error_category": record.error_category,
            "mistake_type": record.mistake_type,
            "description": record.description,
            "what_went_wrong": record.what_went_wrong,
            "correct_approach": record.correct_approach,
            "severity": record.severity,
            "lesson_learned": record.lesson_learned,
            "prevention_checklist": record.prevention_checklist,
            "detected_by": record.detected_by,
            "created_at": record.created_at,
            "time_to_detect_hours": record.time_to_detect_hours,
            "impacted_tickets": record.impacted_tickets,
        }

        try:
            await self._memory.add_correction(
                correction=record,
                dense_vector=dense_vector,
                sparse_vector=sparse_vector,
            )
            logger.info(
                "Indexed correction %s (ticket=%s, type=%s)",
                point_id,
                record.jira_ticket,
                record.mistake_type,
            )
        except Exception as exc:
            logger.error("Failed to index correction: %s", exc)
            raise

        return point_id

    # ── Private ───────────────────────────────────────────────────────────────

    async def _embed(self, text: str) -> list[float]:
        """Generate Voyage AI dense embedding, falling back to heuristic."""
        if not self._voyage_api_key:
            return self._heuristic_embed(text)

        try:
            import httpx

            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    "https://api.voyageai.com/v1/embeddings",
                    headers={
                        "Authorization": f"Bearer {self._voyage_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "input": [text],
                        "model": self._voyage_model,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                return data["data"][0]["embedding"]
        except Exception as exc:
            logger.warning("Voyage AI embed failed: %s; using heuristic", exc)
            return self._heuristic_embed(text)

    def _heuristic_embed(self, text: str) -> list[float]:
        """Deterministic 1024-dim hash embedding for offline/test mode."""
        digest = hashlib.sha256(text.encode()).hexdigest()
        seed = int(digest, 16)
        vec: list[float] = []
        for i in range(_VECTOR_DIM):
            x = float((seed >> (i % 64)) & 0xFF) / 255.0
            vec.append(round(x * 2.0 - 1.0, 6))
        return vec

    @staticmethod
    def _bm25_sparse(text: str) -> dict[int, float]:
        """Compute a simple BM25-inspired sparse token vector."""
        import re
        from collections import Counter

        tokens = re.findall(r"[a-zA-Z0-9_]{3,}", text.lower())
        tf = Counter(tokens)
        total = max(sum(tf.values()), 1)
        result: dict[int, float] = {}
        for token, count in tf.items():
            token_id = int(hashlib.md5(token.encode()).hexdigest(), 16) % 65536
            tf_score = count / total
            idf = log(1.0 + 1.0 / (count + 1.0))
            result[token_id] = round(tf_score * idf, 6)
        return result
