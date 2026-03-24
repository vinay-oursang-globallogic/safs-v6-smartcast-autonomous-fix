"""
SAFS v6.0 — Chunk Merger

Merges overlapping code chunks retrieved from different sources.

When retrieving code context from multiple paths (GitHub, Code-Index MCP, Qdrant),
we often get overlapping or adjacent chunks. ChunkMerger:
1. Detects overlaps (same file, overlapping line ranges)
2. Merges adjacent/overlapping chunks
3. Expands boundaries for context (±N lines)
4. Deduplicates content

Usage:
    merger = ChunkMerger(context_lines=5)
    merged = merger.merge_chunks(chunks)
"""

import logging
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional

logger = logging.getLogger(__name__)


@dataclass
class CodeChunk:
    """
    Code chunk with location info.
    """
    repo: str
    file_path: str
    start_line: int
    end_line: int
    content: str
    source: str  # "path_a", "path_b", "path_c", etc.
    confidence: float = 0.0
    
    def overlaps(self, other: "CodeChunk") -> bool:
        """Check if this chunk overlaps with another."""
        if self.repo != other.repo or self.file_path != other.file_path:
            return False
        
        # Check line range overlap
        return not (self.end_line < other.start_line or self.start_line > other.end_line)
    
    def adjacent(self, other: "CodeChunk", max_gap: int = 5) -> bool:
        """Check if this chunk is adjacent to another (within max_gap lines)."""
        if self.repo != other.repo or self.file_path != other.file_path:
            return False
        
        # Check if within max_gap lines
        gap = min(
            abs(self.start_line - other.end_line),
            abs(other.start_line - self.end_line),
        )
        
        return gap <= max_gap
    
    def merge_with(self, other: "CodeChunk") -> "CodeChunk":
        """Merge this chunk with another overlapping/adjacent chunk."""
        if self.repo != other.repo or self.file_path != other.file_path:
            raise ValueError("Cannot merge chunks from different files")
        
        # Merge line ranges
        new_start = min(self.start_line, other.start_line)
        new_end = max(self.end_line, other.end_line)
        
        # Merge content (prefer longer content, or first if same length)
        if len(self.content) >= len(other.content):
            new_content = self.content
            new_source = self.source
        else:
            new_content = other.content
            new_source = other.source
        
        # Take max confidence
        new_confidence = max(self.confidence, other.confidence)
        
        return CodeChunk(
            repo=self.repo,
            file_path=self.file_path,
            start_line=new_start,
            end_line=new_end,
            content=new_content,
            source=f"{self.source}+{other.source}",
            confidence=new_confidence,
        )


class ChunkMerger:
    """
    Merges overlapping and adjacent code chunks.
    
    Algorithm:
    1. Group chunks by (repo, file_path)
    2. For each file, sort chunks by start_line
    3. Merge overlapping/adjacent chunks
    4. Optionally expand boundaries for context
    """
    
    def __init__(
        self,
        context_lines: int = 5,
        max_gap: int = 5,
        max_chunk_size: int = 500,
    ):
        """
        Initialize ChunkMerger.
        
        Args:
            context_lines: Number of lines to include before/after for context
            max_gap: Maximum line gap to consider chunks adjacent
            max_chunk_size: Maximum lines in merged chunk (prevent giant chunks)
        """
        self.context_lines = context_lines
        self.max_gap = max_gap
        self.max_chunk_size = max_chunk_size
    
    def merge_chunks(
        self,
        chunks: List[CodeChunk],
    ) -> List[CodeChunk]:
        """
        Merge overlapping and adjacent code chunks.
        
        Args:
            chunks: List of CodeChunk objects
        
        Returns:
            List of merged CodeChunk objects
        """
        if not chunks:
            logger.warning("No chunks provided to merge")
            return []
        
        # Group chunks by (repo, file_path)
        file_groups: Dict[Tuple[str, str], List[CodeChunk]] = {}
        
        for chunk in chunks:
            key = (chunk.repo, chunk.file_path)
            if key not in file_groups:
                file_groups[key] = []
            file_groups[key].append(chunk)
        
        # Merge chunks within each file
        merged_chunks = []
        
        for (repo, file_path), file_chunks in file_groups.items():
            # Sort by start_line
            file_chunks.sort(key=lambda c: c.start_line)
            
            # Merge overlapping/adjacent chunks
            merged_file_chunks = self._merge_file_chunks(file_chunks)
            
            merged_chunks.extend(merged_file_chunks)
        
        logger.info(
            f"Merged {len(chunks)} chunks into {len(merged_chunks)} chunks "
            f"across {len(file_groups)} files"
        )
        
        return merged_chunks
    
    def _merge_file_chunks(
        self,
        file_chunks: List[CodeChunk],
    ) -> List[CodeChunk]:
        """
        Merge chunks within a single file.
        
        Args:
            file_chunks: List of chunks from same file, sorted by start_line
        
        Returns:
            List of merged chunks
        """
        if not file_chunks:
            return []
        
        merged = []
        current = file_chunks[0]
        
        for next_chunk in file_chunks[1:]:
            # Check if current and next overlap or are adjacent
            if current.overlaps(next_chunk) or current.adjacent(next_chunk, self.max_gap):
                # Merge chunks
                current = current.merge_with(next_chunk)
                
                # Check if merged chunk is too large
                chunk_size = current.end_line - current.start_line + 1
                if chunk_size > self.max_chunk_size:
                    logger.warning(
                        f"Merged chunk exceeds max size ({chunk_size} > {self.max_chunk_size}), "
                        f"splitting at {current.file_path}:{current.start_line}-{current.end_line}"
                    )
                    # Keep current chunk, start new with next
                    merged.append(current)
                    current = next_chunk
            else:
                # No overlap/adjacency, save current and move to next
                merged.append(current)
                current = next_chunk
        
        # Add last chunk
        merged.append(current)
        
        return merged
    
    def expand_context(
        self,
        chunk: CodeChunk,
        full_file_content: Optional[str] = None,
    ) -> CodeChunk:
        """
        Expand chunk boundaries to include context lines.
        
        Args:
            chunk: CodeChunk to expand
            full_file_content: Optional full file content (for accurate expansion)
        
        Returns:
            Expanded CodeChunk
        """
        # Expand line range
        new_start = max(1, chunk.start_line - self.context_lines)
        new_end = chunk.end_line + self.context_lines
        
        # If full file content provided, extract expanded content
        if full_file_content:
            lines = full_file_content.split('\n')
            
            # Adjust end if beyond file length
            new_end = min(new_end, len(lines))
            
            # Extract expanded content (1-indexed to 0-indexed)
            expanded_content = '\n'.join(lines[new_start - 1:new_end])
            
            return CodeChunk(
                repo=chunk.repo,
                file_path=chunk.file_path,
                start_line=new_start,
                end_line=new_end,
                content=expanded_content,
                source=chunk.source,
                confidence=chunk.confidence,
            )
        else:
            # No full content, just update line numbers
            return CodeChunk(
                repo=chunk.repo,
                file_path=chunk.file_path,
                start_line=new_start,
                end_line=new_end,
                content=chunk.content,  # Keep original content
                source=chunk.source,
                confidence=chunk.confidence,
            )
    
    def deduplicate_chunks(
        self,
        chunks: List[CodeChunk],
    ) -> List[CodeChunk]:
        """
        Remove duplicate chunks (same repo, file, line range).
        
        When duplicates exist, keep the one with highest confidence.
        
        Args:
            chunks: List of CodeChunk objects
        
        Returns:
            Deduplicated list
        """
        if not chunks:
            return []
        
        # Group by (repo, file_path, start_line, end_line)
        seen: Dict[Tuple[str, str, int, int], CodeChunk] = {}
        
        for chunk in chunks:
            key = (chunk.repo, chunk.file_path, chunk.start_line, chunk.end_line)
            
            if key not in seen:
                seen[key] = chunk
            else:
                # Keep chunk with higher confidence
                if chunk.confidence > seen[key].confidence:
                    seen[key] = chunk
        
        deduplicated = list(seen.values())
        
        if len(deduplicated) < len(chunks):
            logger.info(
                f"Deduplicated {len(chunks)} chunks to {len(deduplicated)} chunks"
            )
        
        return deduplicated
