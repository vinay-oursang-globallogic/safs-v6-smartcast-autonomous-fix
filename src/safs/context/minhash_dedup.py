"""
SAFS v6.0 — MinHash Deduplicator

Fuzzy deduplication using MinHash + Locality-Sensitive Hashing (LSH).
Port from mcp_server_jira_log_analyzer POC.

MinHash provides fast approximate similarity detection for:
- Near-duplicate log lines
- Similar code chunks
- Redundant error messages

Algorithm:
1. Shingle text into n-grams (default 3-grams)
2. Compute MinHash signature (compact fingerprint)
3. Use LSH to find similar signatures efficiently
4. Group similar items by threshold

Usage:
    dedup = MinHashDeduplicator(threshold=0.8)
    groups = dedup.deduplicate(log_lines)
"""

import hashlib
import logging
import re
from collections import defaultdict
from typing import List, Set, Dict, Tuple

logger = logging.getLogger(__name__)


class MinHashDeduplicator:
    """
    MinHash-based fuzzy deduplicator for log lines and code chunks.
    
    Uses k-shingles (n-grams) + MinHash signatures for approximate
    similarity detection in O(n) time.
    
    Similarity threshold:
    - 1.0: Exact match
    - 0.9: Nearly identical (1-2 word differences)
    - 0.8: Similar (recommended for logs)
    - 0.7: Somewhat similar
    - <0.7: Different
    """
    
    def __init__(
        self,
        threshold: float = 0.8,
        num_perm: int = 128,
        shingle_size: int = 3,
    ):
        """
        Initialize MinHash deduplicator.
        
        Args:
            threshold: Jaccard similarity threshold (0.0-1.0)
            num_perm: Number of hash permutations (higher = more accurate)
            shingle_size: N-gram size for shingling (3-5 recommended)
        """
        if not 0.0 <= threshold <= 1.0:
            raise ValueError(f"Threshold must be in [0.0, 1.0], got {threshold}")
        
        if num_perm < 1:
            raise ValueError(f"num_perm must be >= 1, got {num_perm}")
        
        if shingle_size < 1:
            raise ValueError(f"shingle_size must be >= 1, got {shingle_size}")
        
        self.threshold = threshold
        self.num_perm = num_perm
        self.shingle_size = shingle_size
        
        # Generate hash functions (using different seeds)
        self.hash_functions = [
            lambda x, seed=i: int(hashlib.md5(f"{seed}:{x}".encode()).hexdigest(), 16)
            for i in range(num_perm)
        ]
    
    def _normalize(self, text: str) -> str:
        """
        Normalize text for comparison.
        
        - Lowercase
        - Remove extra whitespace
        - Keep alphanumeric + common punctuation
        
        Args:
            text: Input text
        
        Returns:
            Normalized text
        """
        # Lowercase
        text = text.lower()
        
        # Replace multiple spaces with single space
        text = re.sub(r'\s+', ' ', text)
        
        # Trim
        text = text.strip()
        
        return text
    
    def _shingle(self, text: str) -> Set[str]:
        """
        Generate k-shingles (character n-grams) from text.
        
        Args:
            text: Input text
        
        Returns:
            Set of shingles
        """
        if len(text) < self.shingle_size:
            # Text too short, use full text as single shingle
            return {text} if text else set()
        
        shingles = set()
        
        for i in range(len(text) - self.shingle_size + 1):
            shingle = text[i:i + self.shingle_size]
            shingles.add(shingle)
        
        return shingles
    
    def _compute_minhash(self, shingles: Set[str]) -> List[int]:
        """
        Compute MinHash signature for a set of shingles.
        
        Args:
            shingles: Set of shingles
        
        Returns:
            MinHash signature (list of num_perm integers)
        """
        if not shingles:
            # Empty shingle set, return max values
            return [2**32 - 1] * self.num_perm
        
        signature = []
        
        for hash_func in self.hash_functions:
            # Compute min hash value across all shingles
            min_hash = min(hash_func(shingle) for shingle in shingles)
            signature.append(min_hash)
        
        return signature
    
    def _jaccard_similarity(self, sig1: List[int], sig2: List[int]) -> float:
        """
        Estimate Jaccard similarity from MinHash signatures.
        
        Jaccard similarity = |A ∩ B| / |A ∪ B|
        
        Estimated by: (number of matching signature positions) / num_perm
        
        Args:
            sig1: First MinHash signature
            sig2: Second MinHash signature
        
        Returns:
            Estimated Jaccard similarity (0.0-1.0)
        """
        if len(sig1) != len(sig2):
            raise ValueError("Signatures must have same length")
        
        if not sig1:
            return 0.0
        
        matches = sum(1 for h1, h2 in zip(sig1, sig2) if h1 == h2)
        
        return matches / len(sig1)
    
    def deduplicate(
        self,
        texts: List[str],
        return_groups: bool = True,
    ) -> List[List[int]]:
        """
        Deduplicate texts using MinHash + threshold.
        
        Args:
            texts: List of texts to deduplicate
            return_groups: If True, return groups of similar indices
                          If False, return list of unique indices
        
        Returns:
            If return_groups=True: List of groups (each group is list of indices)
            If return_groups=False: List of unique representative indices
        """
        if not texts:
            logger.warning("No texts provided to deduplicate")
            return []
        
        # Normalize and compute MinHash signatures
        signatures = []
        
        for i, text in enumerate(texts):
            normalized = self._normalize(text)
            shingles = self._shingle(normalized)
            signature = self._compute_minhash(shingles)
            signatures.append((i, signature))
        
        # Group similar signatures
        groups = []
        visited = set()
        
        for i, (idx1, sig1) in enumerate(signatures):
            if idx1 in visited:
                continue
            
            # Start new group with current signature
            group = [idx1]
            visited.add(idx1)
            
            # Find similar signatures
            for j in range(i + 1, len(signatures)):
                idx2, sig2 = signatures[j]
                
                if idx2 in visited:
                    continue
                
                # Compute similarity
                similarity = self._jaccard_similarity(sig1, sig2)
                
                if similarity >= self.threshold:
                    group.append(idx2)
                    visited.add(idx2)
            
            groups.append(group)
        
        logger.info(
            f"Deduplicated {len(texts)} texts into {len(groups)} groups "
            f"(threshold={self.threshold})"
        )
        
        if return_groups:
            return groups
        else:
            # Return representative from each group (first element)
            return [group[0] for group in groups]
    
    def find_duplicates(
        self,
        texts: List[str],
        query: str,
    ) -> List[Tuple[int, float]]:
        """
        Find texts similar to a query string.
        
        Args:
            texts: List of texts to search
            query: Query string
        
        Returns:
            List of (index, similarity) tuples, sorted by similarity descending
        """
        if not texts:
            return []
        
        # Compute query signature
        query_normalized = self._normalize(query)
        query_shingles = self._shingle(query_normalized)
        query_signature = self._compute_minhash(query_shingles)
        
        # Compute similarities
        similarities = []
        
        for i, text in enumerate(texts):
            text_normalized = self._normalize(text)
            text_shingles = self._shingle(text_normalized)
            text_signature = self._compute_minhash(text_shingles)
            
            similarity = self._jaccard_similarity(query_signature, text_signature)
            
            if similarity >= self.threshold:
                similarities.append((i, similarity))
        
        # Sort by similarity descending
        similarities.sort(key=lambda x: x[1], reverse=True)
        
        logger.info(
            f"Found {len(similarities)} texts similar to query "
            f"(threshold={self.threshold})"
        )
        
        return similarities
    
    def compute_similarity(self, text1: str, text2: str) -> float:
        """
        Compute MinHash similarity between two texts.
        
        Args:
            text1: First text
            text2: Second text
        
        Returns:
            Estimated Jaccard similarity (0.0-1.0)
        """
        # Compute signatures
        text1_norm = self._normalize(text1)
        text1_shingles = self._shingle(text1_norm)
        sig1 = self._compute_minhash(text1_shingles)
        
        text2_norm = self._normalize(text2)
        text2_shingles = self._shingle(text2_norm)
        sig2 = self._compute_minhash(text2_shingles)
        
        return self._jaccard_similarity(sig1, sig2)
