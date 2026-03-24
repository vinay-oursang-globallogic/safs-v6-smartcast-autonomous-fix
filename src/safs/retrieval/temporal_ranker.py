"""
Temporal Weighted Retrieval - Category-specific decay for institutional memory.

NEW in v6.0 — applies category-specific half-lives to down-rank old fixes
while NEVER deleting them (Master Prompt Rule #24).

Master Prompt Reference: Section 4.2.2 - TemporallyWeightedRetrieval
"""

from enum import Enum
from typing import Any
from datetime import datetime, timezone


class ErrorCategory(str, Enum):
    """
    Error categories with distinct temporal decay patterns.
    
    NEW in v6.0 — Master Prompt Appendix B defines 27 categories
    with category-specific half-lives based on stability.
    """
    
    # Hardware/Firmware (very stable, long half-lives)
    LOKI_SEGFAULT = "LOKI_SEGFAULT"  # 730 days
    HDMI_CEC_CONFLICT = "HDMI_CEC_CONFLICT"  # 365 days
    DSP_AUDIO_DROPOUT = "DSP_AUDIO_DROPOUT"  # 365 days
    
    # DRM/Security (moderately stable)
    EME_DRM_FAILURE = "EME_DRM_FAILURE"  # 180 days
    WIDEVINE_PROVISIONING = "WIDEVINE_PROVISIONING"  # 180 days
    
    # Library/Protocol (moderate decay)
    COMPANION_LIB_TIMING = "COMPANION_LIB_TIMING"  # 90 days
    WEBSOCKET_CLOSE_4000 = "WEBSOCKET_CLOSE_4000"  # 90 days
    GRPC_DEADLINE_EXCEEDED = "GRPC_DEADLINE_EXCEEDED"  # 90 days
    
    # Network/API (faster decay)
    HTTP_CLIENT_TIMEOUT = "HTTP_CLIENT_TIMEOUT"  # 60 days
    DNS_RESOLUTION_TIMEOUT = "DNS_RESOLUTION_TIMEOUT"  # 60 days
    
    # App Integration (fast decay)
    NETFLIX_DIAL_MISMATCH = "NETFLIX_DIAL_MISMATCH"  # 45 days
    YOUTUBE_ATV_CODEC_ERR = "YOUTUBE_ATV_CODEC_ERR"  # 45 days
    
    # Memory/Resource (very fast decay)
    OOM_FD_EXHAUSTION = "OOM_FD_EXHAUSTION"  # 30 days
    MEMORY_LEAK = "MEMORY_LEAK"  # 30 days
    
    # Default for uncategorized
    UNKNOWN = "UNKNOWN"  # 90 days (moderate default)


# Master Prompt Appendix B: Category-specific half-lives
DECAY_HALFLIFE: dict[ErrorCategory, int] = {
    # Hardware/Firmware (730d)
    ErrorCategory.LOKI_SEGFAULT: 730,
    ErrorCategory.HDMI_CEC_CONFLICT: 365,
    ErrorCategory.DSP_AUDIO_DROPOUT: 365,
    
    # DRM/Security (180d)
    ErrorCategory.EME_DRM_FAILURE: 180,
    ErrorCategory.WIDEVINE_PROVISIONING: 180,
    
    # Library/Protocol (90d)
    ErrorCategory.COMPANION_LIB_TIMING: 90,
    ErrorCategory.WEBSOCKET_CLOSE_4000: 90,
    ErrorCategory.GRPC_DEADLINE_EXCEEDED: 90,
    
    # Network/API (60d)
    ErrorCategory.HTTP_CLIENT_TIMEOUT: 60,
    ErrorCategory.DNS_RESOLUTION_TIMEOUT: 60,
    
    # App Integration (45d)
    ErrorCategory.NETFLIX_DIAL_MISMATCH: 45,
    ErrorCategory.YOUTUBE_ATV_CODEC_ERR: 45,
    
    # Memory/Resource (30d)
    ErrorCategory.OOM_FD_EXHAUSTION: 30,
    ErrorCategory.MEMORY_LEAK: 30,
    
    # Default
    ErrorCategory.UNKNOWN: 90,
}


class TemporallyWeightedRetrieval:
    """
    Applies temporal decay to search results based on fix age.
    
    Master Prompt Rule #24:
    Records NEVER deleted, only down-ranked. Recent fixes weighted
    higher for fast-changing APIs/libraries.
    
    Decay Formula:
    weight = 1.0 / ((age_days / half_life) + 1.0)
    
    Examples:
    - 0 days old: weight = 1.0 (100% relevance)
    - half_life days old: weight = 0.5 (50% relevance)
    - 2x half_life: weight = 0.333 (33% relevance)
    """

    def __init__(self):
        """Initialize temporal ranker."""
        self.decay_halflife = DECAY_HALFLIFE

    def decay_weight(self, age_days: float, category: ErrorCategory) -> float:
        """
        Calculate decay weight for a fix based on age and category.
        
        Args:
            age_days: Age of fix in days
            category: Error category determining half-life
        
        Returns:
            Decay weight between 0.0 and 1.0
        """
        half_life = self.decay_halflife.get(category, 90)
        
        # Master Prompt Formula: 1.0 / ((age / half_life) + 1.0)
        weight = 1.0 / ((age_days / half_life) + 1.0)
        
        return weight

    def rerank(
        self,
        results: list[dict[str, Any]],
        category: ErrorCategory,
        score_key: str = "score",
        date_key: str = "fixed_at",
    ) -> list[dict[str, Any]]:
        """
        Re-rank search results with temporal decay applied.
        
        Applies POST-RRF re-ranking (Master Prompt Section 4.2.2):
        1. Original RRF score computed (e.g., from Qdrant)
        2. Temporal decay weight calculated
        3. Final score = RRF_score * decay_weight
        4. Results sorted by final score
        
        Args:
            results: List of search results with scores and dates
            category: Error category for half-life lookup
            score_key: Key name for original score (default: "score")
            date_key: Key name for fix date (default: "fixed_at")
        
        Returns:
            Re-ranked results with temporal weights applied
        """
        now = datetime.now(timezone.utc)
        reranked = []
        
        for result in results:
            # Get original score
            original_score = result.get(score_key, 0.0)
            
            # Calculate age in days
            fixed_at = result.get(date_key)
            if isinstance(fixed_at, str):
                fixed_at = datetime.fromisoformat(fixed_at.replace("Z", "+00:00"))
            elif isinstance(fixed_at, datetime):
                if fixed_at.tzinfo is None:
                    fixed_at = fixed_at.replace(tzinfo=timezone.utc)
            else:
                # No date provided — assume recent (weight = 1.0)
                fixed_at = now
            
            age_days = (now - fixed_at).total_seconds() / 86400.0
            
            # Calculate decay weight
            weight = self.decay_weight(age_days, category)
            
            # Apply temporal decay to score
            final_score = original_score * weight
            
            # Enrich result with metadata
            enriched = {
                **result,
                "original_score": original_score,
                "decay_weight": weight,
                "age_days": age_days,
                "final_score": final_score,
            }
            
            reranked.append(enriched)
        
        # Sort by final score descending
        reranked.sort(key=lambda x: x["final_score"], reverse=True)
        
        return reranked

    def get_half_life(self, category: ErrorCategory) -> int:
        """
        Get half-life for a category.
        
        Args:
            category: Error category
        
        Returns:
            Half-life in days
        """
        return self.decay_halflife.get(category, 90)
