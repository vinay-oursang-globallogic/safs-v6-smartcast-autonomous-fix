"""
SAFS v6.0 — Context Analyzer

Context relevance scoring and keyword mapping.
Port from mcp_server_jira_log_analyzer POC.

Maps user-facing ticket descriptions to technical keywords:
- "app freezes" → ["deadlock", "hang", "timeout", "race_condition"]
- "black screen" → ["display_init", "gpu_fault", "video_decode"]
- "no sound" → ["audio_codec", "alsa", "dsp_crash"]

Also scores code chunks for relevance to root cause.

Usage:
    analyzer = ContextAnalyzer()
    keywords = analyzer.extract_keywords(ticket_description)
    relevance = analyzer.score_relevance(code_chunk, root_cause)
"""

import logging
import re
from typing import List, Dict, Set, Tuple

logger = logging.getLogger(__name__)


class ContextAnalyzer:
    """
    Analyzes ticket context and scores code chunk relevance.
    
    Two main functions:
    1. Keyword extraction: Map user language → technical terms
    2. Relevance scoring: Score code chunks against root cause
    """
    
    # User keyword → Technical keyword mappings
    KEYWORD_MAPPINGS = {
        # App behavior
        "freeze": ["deadlock", "hang", "timeout", "race_condition", "mutex", "spinlock"],
        "freezes": ["deadlock", "hang", "timeout", "race_condition", "mutex", "spinlock"],
        "frozen": ["deadlock", "hang", "timeout", "race_condition", "mutex", "spinlock"],
        "hang": ["deadlock", "hang", "timeout", "race_condition"],
        "hangs": ["deadlock", "hang", "timeout", "race_condition"],
        "stuck": ["deadlock", "hang", "timeout", "race_condition"],
        "unresponsive": ["deadlock", "hang", "timeout", "event_loop"],
        "slow": ["performance", "latency", "memory_leak", "cpu_usage"],
        
        # Crash/termination
        "crash": ["segfault", "sigsegv", "sigabrt", "exception", "abort", "core_dump"],
        "crashes": ["segfault", "sigsegv", "sigabrt", "exception", "abort", "core_dump"],
        "crashed": ["segfault", "sigsegv", "sigabrt", "exception", "abort", "core_dump"],
        "reboot": ["kernel_panic", "watchdog", "reboot", "crash"],
        "reboots": ["kernel_panic", "watchdog", "reboot", "crash"],
        "restart": ["crash", "abort", "restart", "exception"],
        "restarts": ["crash", "abort", "restart", "exception"],
        
        # Display
        "black_screen": ["display_init", "gpu_fault", "video_decode", "hdmi", "framebuffer"],
        "black": ["display_init", "gpu_fault", "video_decode", "hdmi", "framebuffer"],
        "blank_screen": ["display_init", "gpu_fault", "video_decode", "hdmi"],
        "no_video": ["video_decode", "codec", "vdec", "gpu_fault"],
        "flickering": ["vsync", "framebuffer", "gpu_fault", "hdmi"],
        "tearing": ["vsync", "framebuffer", "gpu_fault"],
        
        # Audio
        "no_sound": ["audio_codec", "alsa", "dsp_crash", "audio_init", "hdmi_audio"],
        "no_audio": ["audio_codec", "alsa", "dsp_crash", "audio_init", "hdmi_audio"],
        "audio_stutter": ["audio_buffer", "underrun", "dsp_crash", "audio_sync"],
        "audio_delay": ["audio_sync", "latency", "audio_buffer"],
        "crackling": ["audio_buffer", "underrun", "audio_codec"],
        
        # Network/streaming
        "buffering": ["network_timeout", "fetch_timeout", "bandwidth", "cdn"],
        "loading": ["network_timeout", "fetch_timeout", "cache", "latency"],
        "timeout": ["timeout", "fetch_timeout", "network_timeout", "connection"],
        "connection_error": ["network_timeout", "fetch_timeout", "socket", "connection"],
        
        # Input
        "remote_not_working": ["ir_routing", "keydown", "event_handler", "input"],
        "button_not_responding": ["keydown", "event_handler", "input", "focus"],
        "no_input": ["ir_routing", "keydown", "event_handler", "input"],
        
        # DRM/playback
        "drm_error": ["eme_drm", "widevine", "playready", "license", "cdm"],
        "license_error": ["eme_drm", "widevine", "playready", "license"],
        "playback_failed": ["video_decode", "codec", "mse", "drm", "eme"],
        "cant_play": ["video_decode", "codec", "drm", "eme", "mse"],
        
        # App-specific
        "netflix_error": ["netflix_msl", "drm", "playback", "eme"],
        "hulu_error": ["hulu_ad", "mse", "playback"],
        "youtube_error": ["video_decode", "codec", "playback"],
        "companion_error": ["companion_lib", "timing_race", "init_failure"],
        
        # Memory
        "out_of_memory": ["heap_oom", "memory_leak", "malloc", "oom"],
        "memory_error": ["heap_oom", "memory_leak", "malloc", "segfault"],
        "leak": ["memory_leak", "fd_leak", "resource_leak"],
    }
    
    # Technical keyword → Weight (for relevance scoring)
    KEYWORD_WEIGHTS = {
        # High priority (critical errors)
        "segfault": 1.0,
        "sigsegv": 1.0,
        "sigabrt": 1.0,
        "kernel_panic": 1.0,
        "heap_oom": 1.0,
        "deadlock": 1.0,
        
        # Medium priority (common issues)
        "timeout": 0.8,
        "race_condition": 0.8,
        "memory_leak": 0.8,
        "drm": 0.7,
        "eme": 0.7,
        "video_decode": 0.7,
        
        # Lower priority (generic)
        "error": 0.5,
        "warning": 0.3,
        "info": 0.2,
    }
    
    def __init__(self):
        """Initialize ContextAnalyzer."""
        pass
    
    def extract_keywords(
        self,
        description: str,
        top_k: int = 10,
    ) -> List[str]:
        """
        Extract technical keywords from ticket description.
        
        Maps user-facing language to technical terms using KEYWORD_MAPPINGS.
        
        Args:
            description: Jira ticket description
            top_k: Maximum number of keywords to return
        
        Returns:
            List of technical keywords
        """
        if not description:
            logger.warning("Empty description provided to extract_keywords")
            return []
        
        # Normalize text
        text = description.lower().strip()
        
        # Replace common punctuation with spaces
        text = re.sub(r'[^\w\s]', ' ', text)
        
        # Extract keywords
        keywords: Set[str] = set()
        
        # Check each mapping
        for user_term, tech_keywords in self.KEYWORD_MAPPINGS.items():
            # Check if user term appears in description
            pattern = r'\b' + re.escape(user_term) + r'\b'
            if re.search(pattern, text):
                keywords.update(tech_keywords)
        
        # Also extract technical keywords already present
        for tech_keyword in self.KEYWORD_WEIGHTS.keys():
            pattern = r'\b' + re.escape(tech_keyword) + r'\b'
            if re.search(pattern, text):
                keywords.add(tech_keyword)
        
        # Convert to sorted list (by weight descending)
        keywords_list = sorted(
            keywords,
            key=lambda k: self.KEYWORD_WEIGHTS.get(k, 0.5),
            reverse=True,
        )
        
        result = keywords_list[:top_k]
        
        logger.info(
            f"Extracted {len(result)} technical keywords from description "
            f"(matched {len(keywords)} total)"
        )
        
        return result
    
    def score_relevance(
        self,
        code_chunk: str,
        root_cause: str,
        keywords: List[str],
    ) -> float:
        """
        Score relevance of a code chunk to root cause analysis.
        
        Algorithm:
        1. Normalize code chunk and root cause
        2. Extract symbols/keywords from both
        3. Compute overlap score (weighted by keyword importance)
        4. Boost score if specific functions/files mentioned in root cause appear
        
        Args:
            code_chunk: Code snippet (file content)
            root_cause: Root cause analysis (Markdown)
            keywords: Technical keywords from ticket
        
        Returns:
            Relevance score (0.0-1.0)
        """
        if not code_chunk:
            return 0.0
        
        # Normalize texts
        code_lower = code_chunk.lower()
        root_cause_lower = root_cause.lower()
        
        score = 0.0
        
        # Score 1: Keyword overlap
        keyword_score = 0.0
        matched_keywords = 0
        
        for keyword in keywords:
            weight = self.KEYWORD_WEIGHTS.get(keyword, 0.5)
            
            if keyword in code_lower:
                keyword_score += weight
                matched_keywords += 1
        
        # Normalize keyword score
        if keywords:
            keyword_score /= len(keywords)
        
        score += keyword_score * 0.4  # 40% weight
        
        # Score 2: Root cause mention overlap
        # Extract potential function/file names from root cause
        function_pattern = r'\b([a-z_][a-z0-9_]*)\s*\('
        file_pattern = r'\b([a-z_][a-z0-9_]*\.(?:py|js|cpp|c|h|java|go|rs))\b'
        
        functions = re.findall(function_pattern, root_cause_lower)
        files = re.findall(file_pattern, root_cause_lower)
        
        mentions = set(functions + files)
        
        mention_score = 0.0
        for mention in mentions:
            if mention in code_lower:
                mention_score += 1.0
        
        # Normalize mention score
        if mentions:
            mention_score /= len(mentions)
        
        score += mention_score * 0.6  # 60% weight
        
        logger.debug(
            f"Relevance score: {score:.3f} "
            f"(keywords: {keyword_score:.3f}, mentions: {mention_score:.3f}, "
            f"matched_keywords: {matched_keywords}/{len(keywords)})"
        )
        
        return min(score, 1.0)  # Cap at 1.0
    
    def rank_chunks(
        self,
        chunks: List[Tuple[str, str]],  # (chunk_id, content)
        root_cause: str,
        keywords: List[str],
        top_k: int = 5,
    ) -> List[Tuple[str, float]]:
        """
        Rank code chunks by relevance to root cause.
        
        Args:
            chunks: List of (chunk_id, content) tuples
            root_cause: Root cause analysis
            keywords: Technical keywords
            top_k: Number of top chunks to return
        
        Returns:
            List of (chunk_id, relevance_score) tuples, sorted by score descending
        """
        if not chunks:
            return []
        
        scored_chunks = []
        
        for chunk_id, content in chunks:
            score = self.score_relevance(content, root_cause, keywords)
            scored_chunks.append((chunk_id, score))
        
        # Sort by score descending
        scored_chunks.sort(key=lambda x: x[1], reverse=True)
        
        result = scored_chunks[:top_k]
        
        logger.info(
            f"Ranked {len(chunks)} chunks, returning top {len(result)} "
            f"(scores: {[f'{s:.3f}' for _, s in result[:3]]})"
        )
        
        return result
