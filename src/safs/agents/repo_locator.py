"""
Repository Locator Agent - Stage 4 of SAFS pipeline.

Identifies target repositories, files, and code locations for bug fixes
using four-path retrieval architecture.

Master Prompt Reference: Section 3.6 - Stage 4: RepoLocatorAgent
"""

import re
from dataclasses import dataclass
from typing import Optional
from ..retrieval.retrieval_router import RetrievalRouter
from ..retrieval.rate_limiter import Priority
# Import ErrorCategory from log_analysis.models (the canonical source) — NOT from
# temporal_ranker, which defines a separate enum with different values.
from ..log_analysis.models import ErrorCategory, RootCauseResult


@dataclass
class CodeLocation:
    """Identified code location for a bug."""
    repo: str
    path: str
    line_number: Optional[int]
    confidence: float  # 0.0 to 1.0
    source: str  # "path_a", "path_b", "path_c", "path_d"
    content_preview: str = ""


@dataclass
class RepoLocatorResult:
    """
    Output from RepoLocatorAgent.
    
    Contains all identified repositories, files, and locations
    ranked by confidence.
    """
    primary_locations: list[CodeLocation]  # Top 3-5 most confident
    secondary_locations: list[CodeLocation]  # Lower confidence alternatives
    similar_fixes: list[dict]  # Historical fixes from PATH C
    known_mistakes: list[dict]  # Known anti-patterns from PATH C
    device_context: Optional[dict] = None  # Live device info from PATH D
    confidence_score: float = 0.0  # Overall locator confidence


class RepoLocatorAgent:
    """
    Stage 4: Repository Locator Agent.
    
    Master Prompt Rule #26:
    RepoLocatorAgent uses four-path retrieval to identify target code
    locations for bug fixes. Combines exact search (PATH A), semantic
    search (PATH B), institutional memory (PATH C), and live device
    introspection (PATH D).
    
    Input: PipelineState + RootCauseResult (from Phase 6)
    Output: RepoLocatorResult (repositories, files, confidence scores)
    """

    def __init__(self, retrieval_router: RetrievalRouter):
        """
        Initialize RepoLocatorAgent.
        
        Args:
            retrieval_router: RetrievalRouter for four-path retrieval
        """
        self.router = retrieval_router

    def _extract_symbols(self, root_cause: RootCauseResult) -> list[str]:
        """
        Extract relevant symbols from root cause analysis.
        
        Args:
            root_cause: RootCauseResult from Stage 3/6 (Root Cause Analysis)
        
        Returns:
            List of symbol names (functions, classes, files)
        """
        symbols = []
        
        # Extract from analysis text
        analysis_text = root_cause.root_cause
        
        # Function names (common patterns: functionName(), function_name())
        func_pattern = r'\b([a-z_][a-z0-9_]*)\s*\('
        symbols.extend(re.findall(func_pattern, analysis_text, re.IGNORECASE))
        
        # Class names (CamelCase)
        class_pattern = r'\b([A-Z][a-zA-Z0-9]+)\b'
        symbols.extend(re.findall(class_pattern, analysis_text))
        
        # File names
        file_pattern = r'\b([a-z_][a-z0-9_]*\.(?:py|js|cpp|c|h|java|go|rs))\b'
        symbols.extend(re.findall(file_pattern, analysis_text, re.IGNORECASE))
        
        # Extract from affected files
        symbols.extend(root_cause.affected_files)
        
        # Deduplicate
        return list(set(symbols))

    async def locate(
        self,
        root_cause: RootCauseResult,
        category: ErrorCategory,
        device_id: Optional[str] = None,
        priority: Optional[Priority] = None,
    ) -> RepoLocatorResult:
        """
        Locate target repositories and files for bug fix.
        
        Args:
            root_cause: Root cause analysis result
            category: Error category from BugLayerRouter
            device_id: Optional device ID for on-device retrieval
            priority: Optional priority for rate limiting (defaults to P1)
        
        Returns:
            RepoLocatorResult with located code and confidence
        
        Multi-Path Retrieval Strategy:
        1. Extract symbols/files from root cause
        2. PATH B: Symbol search (exact AST matches, no rate limits)
        3. PATH A: Code search (keyword search with rate limiting)
        4. PATH C: Similar historical fixes (with temporal decay)
        5. PATH C: Known mistakes to avoid
        6. PATH D: Live device context (if device_id provided)
        """
        # Default to P1 if not specified
        if priority is None:
            priority = Priority.P1
        
        # Step 1: Extract symbols
        symbols = self._extract_symbols(root_cause)
        
        all_locations: list[CodeLocation] = []
        
        # Step 2: PATH B - Symbol search (highest priority, no rate limits)
        for symbol in symbols[:5]:  # Top 5 symbols
            symbol_results = await self.router.symbol_search(symbol)
            
            for result in symbol_results:
                all_locations.append(
                    CodeLocation(
                        repo=result.repo,
                        path=result.path,
                        line_number=result.line_number,
                        confidence=0.85,  # High confidence for exact symbol matches
                        source="path_b_symbol",
                        content_preview=result.content[:200],
                    )
                )
        
        # Step 3: PATH A - Code search (with rate limiting)
        # Use root cause analysis text for search query
        error_message = root_cause.root_cause
        if error_message:
            code_search_results = await self.router.search_code(
                query=error_message[:100],  # Truncate long analysis text
                priority=priority,  # Use priority from caller (Master Prompt Rule #5)
            )
            
            for result in code_search_results[:10]:  # Top 10
                all_locations.append(
                    CodeLocation(
                        repo=result.repo,
                        path=result.path,
                        line_number=result.line_number,
                        confidence=0.65,  # Medium confidence for keyword match
                        source="path_a_search",
                        content_preview=result.content[:200],
                    )
                )
        
        # Step 4: PATH B - Semantic search (fallback if insufficient results)
        if len(all_locations) < 3:
            query = f"{error_message} {' '.join(symbols[:3])}"
            semantic_results = await self.router.semantic_code_search(
                query=query,
                top_k=10,
            )
            
            for result in semantic_results:
                all_locations.append(
                    CodeLocation(
                        repo=result.repo,
                        path=result.path,
                        line_number=result.line_number,
                        confidence=0.70,  # Medium-high for semantic match
                        source="path_b_semantic",
                        content_preview=result.content[:200],
                    )
                )
        
        # Step 5: PATH C - Similar historical fixes
        similar_fixes = await self.router.find_similar_fixes(
            query=error_message,
            category=category,
            top_k=5,
        )
        
        # Enrich locations from similar fixes
        for fix in similar_fixes:
            repo = fix.get("repo")
            path = fix.get("file_path")
            if repo and path:
                all_locations.append(
                    CodeLocation(
                        repo=repo,
                        path=path,
                        line_number=None,
                        confidence=fix.get("final_score", 0.5),
                        source="path_c_similar",
                        content_preview=fix.get("fix_summary", "")[:200],
                    )
                )
        
        # Step 6: PATH C - Known mistakes
        known_mistakes = await self.router.find_known_mistakes(
            query=error_message,
            category=category,
            top_k=3,
        )
        
        # Step 7: PATH D - Device context (if available)
        device_context = None
        if device_id:
            device_context = await self.router.get_device_info(device_id)
        
        # Step 8: Deduplicate and rank by confidence
        unique_locations = self._deduplicate_locations(all_locations)
        ranked_locations = sorted(
            unique_locations,
            key=lambda loc: loc.confidence,
            reverse=True,
        )
        
        # Split into primary (top 5) and secondary
        primary_locations = ranked_locations[:5]
        secondary_locations = ranked_locations[5:15]
        
        # Calculate overall confidence
        overall_confidence = (
            sum(loc.confidence for loc in primary_locations) / len(primary_locations)
            if primary_locations
            else 0.0
        )
        
        return RepoLocatorResult(
            primary_locations=primary_locations,
            secondary_locations=secondary_locations,
            similar_fixes=similar_fixes,
            known_mistakes=known_mistakes,
            device_context=device_context,
            confidence_score=overall_confidence,
        )

    def _deduplicate_locations(
        self, locations: list[CodeLocation]
    ) -> list[CodeLocation]:
        """
        Deduplicate locations, keeping highest confidence for each repo+path.
        
        Args:
            locations: List of code locations
        
        Returns:
            Deduplicated list
        """
        seen = {}
        
        for loc in locations:
            key = (loc.repo, loc.path)
            
            if key not in seen or loc.confidence > seen[key].confidence:
                seen[key] = loc
        
        return list(seen.values())
