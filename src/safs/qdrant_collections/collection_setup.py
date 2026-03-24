"""
SAFS v6.0 — Qdrant Collection Setup

Creates and manages the two hybrid collections:
- historical_fixes: Past successful fixes
- fix_corrections: Past mistakes and egressions

Both use BM25 sparse + voyage-code-3 dense vectors + RRF fusion.
"""

import logging
from typing import Optional

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    Modifier,
    SparseVectorParams,
    VectorParams,
)

logger = logging.getLogger(__name__)


class QdrantCollectionManager:
    """
    Manages Qdrant collection lifecycle (create, delete, check existence).
    """
    
    # Collection names
    HISTORICAL_FIXES = "historical_fixes"
    FIX_CORRECTIONS = "fix_corrections"
    
    # Vector dimensions
    DENSE_DIM = 1024  # voyage-code-3 embedding dimension
    
    def __init__(self, qdrant_url: str = "http://localhost:6333", api_key: Optional[str] = None):
        """
        Initialize Qdrant collection manager.
        
        Args:
            qdrant_url: Qdrant server URL
            api_key: Optional API key for Qdrant Cloud
        """
        self.client = QdrantClient(url=qdrant_url, api_key=api_key)
        logger.info(f"Connected to Qdrant at {qdrant_url}")
    
    def create_collections(self, recreate: bool = False) -> None:
        """
        Create both hybrid collections if they don't exist.
        
        Args:
            recreate: If True, delete existing collections first
        """
        for collection_name in [self.HISTORICAL_FIXES, self.FIX_CORRECTIONS]:
            self.create_collection(collection_name, recreate=recreate)
    
    def create_collection(self, collection_name: str, recreate: bool = False) -> None:
        """
        Create a single hybrid collection with BM25 sparse + dense vectors.
        
        Args:
            collection_name: Name of the collection
            recreate: If True, delete existing collection first
        """
        if recreate and self.collection_exists(collection_name):
            logger.info(f"Deleting existing collection: {collection_name}")
            self.client.delete_collection(collection_name)
        
        if self.collection_exists(collection_name):
            logger.info(f"Collection already exists: {collection_name}")
            return
        
        logger.info(f"Creating hybrid collection: {collection_name}")
        
        self.client.create_collection(
            collection_name=collection_name,
            vectors_config={
                "dense": VectorParams(
                    size=self.DENSE_DIM,
                    distance=Distance.COSINE,
                )
            },
            sparse_vectors_config={
                "sparse": SparseVectorParams(
                    modifier=Modifier.IDF,  # BM25 IDF weighting
                )
            },
        )
        
        logger.info(f"Created collection: {collection_name} (dense={self.DENSE_DIM}, sparse=BM25)")
    
    def collection_exists(self, collection_name: str) -> bool:
        """
        Check if a collection exists.
        
        Args:
            collection_name: Name of the collection
            
        Returns:
            True if collection exists, False otherwise
        """
        try:
            collections = self.client.get_collections().collections
            return any(c.name == collection_name for c in collections)
        except Exception as e:
            logger.error(f"Error checking collection existence: {e}")
            return False
    
    def delete_collection(self, collection_name: str) -> None:
        """
        Delete a collection.
        
        Args:
            collection_name: Name of the collection to delete
        """
        if self.collection_exists(collection_name):
            logger.info(f"Deleting collection: {collection_name}")
            self.client.delete_collection(collection_name)
        else:
            logger.warning(f"Collection does not exist: {collection_name}")
    
    def get_collection_info(self, collection_name: str) -> dict:
        """
        Get information about a collection.
        
        Args:
            collection_name: Name of the collection
            
        Returns:
            Dictionary with collection metadata
        """
        if not self.collection_exists(collection_name):
            return {"exists": False}
        
        info = self.client.get_collection(collection_name)
        
        return {
            "exists": True,
            "name": collection_name,
            "vectors_count": info.vectors_count,
            "points_count": info.points_count,
            "segments_count": info.segments_count,
            "status": info.status,
        }
    
    def setup_all(self, recreate: bool = False) -> dict:
        """
        Setup all collections and return status.
        
        Args:
            recreate: If True, recreate all collections
            
        Returns:
            Dictionary with setup status for each collection
        """
        self.create_collections(recreate=recreate)
        
        return {
            "historical_fixes": self.get_collection_info(self.HISTORICAL_FIXES),
            "fix_corrections": self.get_collection_info(self.FIX_CORRECTIONS),
        }
