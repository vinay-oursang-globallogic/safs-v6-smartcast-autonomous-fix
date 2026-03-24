"""
SAFS v6.0 — Context Builder Agent

Stage 5: Context Assembly
Assembles comprehensive fix context from repository locator results.

Master Prompt Reference: Section 5.2 - Context Builder

Combines:
- Code chunks from RepoLocatorAgent (PATH A/B/C/D)
- Similar historical fixes
- Known mistakes to avoid
- Device registry context (if available)
- Relevance-scored and deduplicated code

Pipeline:
1. Extract code chunks from RepoLocatorResult
2. Score relevance using ContextAnalyzer
3. Merge overlapping chunks using ChunkMerger
4. Deduplicate using MinHashDeduplicator
5. Extract TF-IDF keywords
6. Assemble markdown context summary

Usage:
    agent = ContextBuilderAgent(retrieval_router)
    context = await agent.build_context(
        repo_locator_result=result,
        root_cause_result=root_cause,
        ticket=ticket,
    )
"""

import logging
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import asdict

from ..log_analysis.models import (
    ContextResult,
    PipelineState,
    RootCauseResult,
)
from safs.agents.repo_locator import RepoLocatorResult, CodeLocation
from ..retrieval.retrieval_router import RetrievalRouter

from .context_analyzer import ContextAnalyzer
from .chunk_merger import ChunkMerger, CodeChunk
from .minhash_dedup import MinHashDeduplicator
from .tfidf_scorer import TFIDFScorer

logger = logging.getLogger(__name__)


class ContextBuilderAgent:
    """
    Stage 5: Context Builder Agent.
    
    Orchestrates context assembly for fix generation using:
    - ContextAnalyzer: Relevance scoring
    - ChunkMerger: Merge overlapping chunks
    - MinHashDeduplicator: Fuzzy deduplication
    - TFIDFScorer: Keyword extraction
    
    Input: RepoLocatorResult (from Stage 4)
    Output: ContextResult with assembled context summary
    """
    
    def __init__(
        self,
        retrieval_router: RetrievalRouter,
        context_lines: int = 5,
        max_chunks: int = 10,
        max_context_tokens: int = 15000,
    ):
        """
        Initialize ContextBuilderAgent.
        
        Args:
            retrieval_router: RetrievalRouter for fetching full file contents
            context_lines: Lines of context around code chunks
            max_chunks: Maximum number of chunks to include
            max_context_tokens: Maximum tokens for context summary (rough estimate)
        """
        self.router = retrieval_router
        self.max_chunks = max_chunks
        self.max_context_tokens = max_context_tokens
        
        # Initialize components
        self.analyzer = ContextAnalyzer()
        self.merger = ChunkMerger(context_lines=context_lines)
        self.deduplicator = MinHashDeduplicator(threshold=0.85)
        self.tfidf = TFIDFScorer()
    
    async def build_context(
        self,
        state: PipelineState,
        repo_locator_result: RepoLocatorResult,
        root_cause_result: RootCauseResult,
    ) -> ContextResult:
        """
        Build comprehensive context for fix generation.
        
        Args:
            state: Pipeline state with ticket info
            repo_locator_result: Output from RepoLocatorAgent
            root_cause_result: Output from RootCauseAgent
        
        Returns:
            ContextResult with assembled context
        """
        logger.info(f"Building context for ticket {state.ticket.key}")
        
        # Step 1: Extract technical keywords from ticket
        keywords = self.analyzer.extract_keywords(
            state.ticket.description or "",
            top_k=10,
        )
        
        logger.info(f"Extracted {len(keywords)} technical keywords: {keywords[:5]}")
        
        # Step 2: Convert CodeLocation to CodeChunk
        chunks = await self._locations_to_chunks(repo_locator_result.primary_locations)
        
        logger.info(f"Converted {len(chunks)} locations to code chunks")
        
        # Step 3: Score relevance
        scored_chunks = self._score_chunks(
            chunks,
            root_cause_result.root_cause,
            keywords,
        )
        
        logger.info(
            f"Scored {len(scored_chunks)} chunks, "
            f"top scores: {[f'{s:.3f}' for _, s in scored_chunks[:3]]}"
        )
        
        # Step 4: Filter by relevance threshold
        relevant_chunks = [
            chunk for chunk, score in scored_chunks
            if score >= 0.3  # Minimum relevance threshold
        ]
        
        logger.info(f"Filtered to {len(relevant_chunks)} relevant chunks (score >= 0.3)")
        
        # Step 5: Merge overlapping chunks
        merged_chunks = self.merger.merge_chunks(relevant_chunks)
        
        logger.info(f"Merged to {len(merged_chunks)} chunks")
        
        # Step 6: Deduplicate using MinHash
        unique_chunk_indices = self.deduplicator.deduplicate(
            [chunk.content for chunk in merged_chunks],
            return_groups=False,
        )
        
        deduplicated_chunks = [merged_chunks[i] for i in unique_chunk_indices]
        
        logger.info(f"Deduplicated to {len(deduplicated_chunks)} unique chunks")
        
        # Step 7: Limit to max_chunks (take highest relevance)
        final_chunks = deduplicated_chunks[:self.max_chunks]
        
        logger.info(f"Limited to {len(final_chunks)} chunks (max_chunks={self.max_chunks})")
        
        # Step 8: Extract TF-IDF keywords from code chunks
        code_keywords = self.tfidf.extract_keywords(
            [chunk.content for chunk in final_chunks],
            top_k=15,
        )
        
        logger.info(
            f"Extracted {len(code_keywords)} TF-IDF keywords from code: "
            f"{[kw for kw, _ in code_keywords[:5]]}"
        )
        
        # Step 9: Assemble context summary (Markdown)
        context_summary = self._build_context_summary(
            ticket=state.ticket,
            root_cause_result=root_cause_result,
            keywords=keywords,
            code_keywords=code_keywords,
            chunks=final_chunks,
            similar_fixes=repo_locator_result.similar_fixes,
            known_mistakes=repo_locator_result.known_mistakes,
            device_context=repo_locator_result.device_context,
        )
        
        logger.info(f"Assembled context summary ({len(context_summary)} chars)")
        
        # Step 10: Build ContextResult
        return ContextResult(
            github_files=[
                {
                    "repo": chunk.repo,
                    "path": chunk.file_path,
                    "lines": f"{chunk.start_line}-{chunk.end_line}",
                    "source": chunk.source,
                    "content": chunk.content[:500],  # Preview
                }
                for chunk in final_chunks
                if "path_a" in chunk.source or "path_b" in chunk.source
            ],
            code_index_results=[
                {
                    "repo": chunk.repo,
                    "path": chunk.file_path,
                    "lines": f"{chunk.start_line}-{chunk.end_line}",
                    "confidence": chunk.confidence,
                }
                for chunk in final_chunks
                if "path_b" in chunk.source
            ],
            qdrant_results=[
                {
                    "fix_id": fix.get("fix_id"),
                    "description": fix.get("description", "")[:200],
                    "confidence": fix.get("confidence", 0.0),
                }
                for fix in repo_locator_result.similar_fixes[:5]
            ],
            registry_data=repo_locator_result.device_context,
            context_summary=context_summary,
            similar_fixes=repo_locator_result.similar_fixes,  # Full list for FixGenerator
            known_mistakes=repo_locator_result.known_mistakes,  # Anti-patterns for FixGenerator
            primary_locations=[
                {
                    "repo": loc.repo,
                    "file_path": loc.path,  # CodeLocation uses 'path' not 'file_path'
                    "line_number": loc.line_number,
                    "confidence": loc.confidence,
                    "match_reason": loc.source,  # CodeLocation uses 'source' not 'match_reason'
                    "content_preview": loc.content_preview,  # Add content preview
                }
                for loc in repo_locator_result.primary_locations
            ],
            code_locations=[  # Add backward compatibility alias
                {
                    "repo": loc.repo,
                    "file_path": loc.path,
                    "line_number": loc.line_number,
                    "confidence": loc.confidence,
                    "match_reason": loc.source,
                    "content_preview": loc.content_preview,
                }
                for loc in repo_locator_result.primary_locations
            ],
        )
    
    async def _locations_to_chunks(
        self,
        locations: List[CodeLocation],
    ) -> List[CodeChunk]:
        """
        Convert CodeLocation objects to CodeChunk objects.
        
        Fetches full file content if needed.
        
        Args:
            locations: List of CodeLocation from RepoLocatorAgent
        
        Returns:
            List of CodeChunk with content
        """
        chunks = []
        
        for loc in locations:
            # Fetch full file content if preview is short or missing
            if len(loc.content_preview) < 100:
                try:
                    # Fetch via router
                    full_content = await self.router.get_file(
                        repo=loc.repo,
                        path=loc.path,
                        ref="main",
                    )
                    if full_content:
                        content = full_content
                    else:
                        content = loc.content_preview
                except Exception as e:
                    logger.warning(f"Failed to fetch full content for {loc.path}: {e}")
                    content = loc.content_preview
            else:
                content = loc.content_preview
            
            chunk = CodeChunk(
                repo=loc.repo,
                file_path=loc.path,
                start_line=loc.line_number or 1,
                end_line=(loc.line_number or 1) + min(50, len(content.split('\n'))),
                content=content,
                source=loc.source,
                confidence=loc.confidence,
            )
            
            chunks.append(chunk)
        
        return chunks
    
    def _score_chunks(
        self,
        chunks: List[CodeChunk],
        root_cause: str,
        keywords: List[str],
    ) -> List[tuple[CodeChunk, float]]:
        """
        Score code chunks by relevance.
        
        Args:
            chunks: List of CodeChunk objects
            root_cause: Root cause analysis
            keywords: Technical keywords
        
        Returns:
            List of (chunk, score) tuples, sorted by score descending
        """
        scored = []
        
        for chunk in chunks:
            score = self.analyzer.score_relevance(
                chunk.content,
                root_cause,
                keywords,
            )
            
            # Boost score by chunk confidence from locator
            combined_score = (score * 0.7) + (chunk.confidence * 0.3)
            
            scored.append((chunk, combined_score))
        
        # Sort by score descending
        scored.sort(key=lambda x: x[1], reverse=True)
        
        return scored
    
    def _build_context_summary(
        self,
        ticket: Any,
        root_cause_result: RootCauseResult,
        keywords: List[str],
        code_keywords: List[Tuple[str, float]],
        chunks: List[CodeChunk],
        similar_fixes: List[Dict[str, Any]],
        known_mistakes: List[Dict[str, Any]],
        device_context: Optional[Dict[str, Any]],
    ) -> str:
        """
        Build comprehensive context summary in Markdown.
        
        Args:
            ticket: Jira ticket
            root_cause_result: Root cause analysis
            keywords: Technical keywords from ticket
            code_keywords: TF-IDF keywords from code
            chunks: Final code chunks
            similar_fixes: Historical fixes
            known_mistakes: Known anti-patterns
            device_context: Device registry data
        
        Returns:
            Markdown-formatted context summary
        """
        sections = []
        
        # Section 1: Ticket Info
        sections.append("# Context Summary\n")
        sections.append(f"**Ticket**: {ticket.key}\n")
        sections.append(f"**Summary**: {ticket.summary}\n")
        sections.append(f"**Description**: {(ticket.description or '')[:500]}...\n")
        sections.append("\n---\n")
        
        # Section 2: Root Cause
        sections.append("## Root Cause Analysis\n")
        sections.append(f"{root_cause_result.root_cause}\n")
        sections.append(f"**Confidence**: {root_cause_result.confidence:.2f}\n")
        sections.append(f"**Category**: {root_cause_result.error_category.value}\n")
        sections.append(f"**Severity**: {root_cause_result.severity.value}\n")
        sections.append("\n---\n")
        
        # Section 3: Keywords
        sections.append("## Technical Keywords\n")
        sections.append(f"**From Ticket**: {', '.join(keywords[:10])}\n")
        sections.append(
            f"**From Code (TF-IDF)**: "
            f"{', '.join([kw for kw, _ in code_keywords[:10]])}\n"
        )
        sections.append("\n---\n")
        
        # Section 4: Code Locations
        sections.append("## Relevant Code Locations\n")
        
        for i, chunk in enumerate(chunks[:5], 1):
            sections.append(f"### {i}. {chunk.file_path} (lines {chunk.start_line}-{chunk.end_line})\n")
            sections.append(f"**Repo**: {chunk.repo}\n")
            sections.append(f"**Source**: {chunk.source}\n")
            sections.append(f"**Confidence**: {chunk.confidence:.2f}\n")
            sections.append(f"```\n{chunk.content[:500]}\n```\n")  # Truncate for summary
            sections.append("\n")
        
        sections.append("\n---\n")
        
        # Section 5: Similar Fixes
        if similar_fixes:
            sections.append("## Similar Historical Fixes\n")
            
            for i, fix in enumerate(similar_fixes[:3], 1):
                sections.append(f"### {i}. {fix.get('description', 'No description')[:100]}\n")
                sections.append(f"**Confidence**: {fix.get('confidence', 0.0):.2f}\n")
                sections.append(f"**Age**: {fix.get('age_days', 'N/A')} days\n")
                sections.append("\n")
            
            sections.append("\n---\n")
        
        # Section 6: Known Mistakes
        if known_mistakes:
            sections.append("## Known Mistakes to Avoid\n")
            
            for i, mistake in enumerate(known_mistakes[:3], 1):
                sections.append(f"### {i}. {mistake.get('description', 'No description')[:100]}\n")
                sections.append(f"**What Went Wrong**: {mistake.get('what_went_wrong', 'N/A')[:200]}\n")
                sections.append(f"**Correct Approach**: {mistake.get('correct_approach', 'N/A')[:200]}\n")
                sections.append("\n")
            
            sections.append("\n---\n")
        
        # Section 7: Device Context
        if device_context:
            sections.append("## Device Context\n")
            sections.append(f"**Firmware**: {device_context.get('firmware_version', 'N/A')}\n")
            sections.append(f"**Model**: {device_context.get('model', 'N/A')}\n")
            sections.append("\n")
        
        context_summary = "".join(sections)
        
        # Truncate if too long (rough token estimate: 4 chars = 1 token)
        max_chars = self.max_context_tokens * 4
        
        if len(context_summary) > max_chars:
            logger.warning(
                f"Context summary too long ({len(context_summary)} chars > {max_chars}), "
                f"truncating"
            )
            context_summary = context_summary[:max_chars] + "\n\n[...truncated]"
        
        return context_summary
    
    async def close(self):
        """Cleanup resources."""
        logger.info("ContextBuilderAgent closed")
