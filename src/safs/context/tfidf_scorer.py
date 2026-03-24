"""
SAFS v6.0 — TF-IDF Scorer

Rare event scoring using TF-IDF (Term Frequency-Inverse Document Frequency).
Port from mcp_server_jira_log_analyzer POC.

TF-IDF identifies rare/important keywords in log lines by:
1. Computing term frequency (TF) in each log line
2. Computing inverse document frequency (IDF) across all logs
3. Ranking terms by TF-IDF score

Higher TF-IDF = rarer, more significant terms.

Usage:
    scorer = TFIDFScorer()
    keywords = scorer.extract_keywords(log_lines, top_k=10)
"""

import logging
import re
from collections import Counter, defaultdict
from math import log
from typing import List, Dict, Tuple

logger = logging.getLogger(__name__)


class TFIDFScorer:
    """
    TF-IDF scorer for extracting rare/significant keywords from logs.
    
    Algorithm:
    1. Tokenize log lines (alphanumeric words)
    2. Compute TF (term frequency) per log line
    3. Compute IDF (inverse document frequency) across all lines
    4. Rank keywords by TF-IDF score
    5. Filter out common stop words
    """
    
    STOP_WORDS = {
        # Common English stop words
        "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
        "of", "with", "by", "from", "as", "is", "was", "are", "be", "been",
        "have", "has", "had", "do", "does", "did", "will", "would", "should",
        "could", "can", "may", "might", "must", "this", "that", "these", "those",
        "it", "its", "i", "you", "he", "she", "we", "they", "what", "which",
        "who", "when", "where", "why", "how",
        
        # Common log words (usually not interesting)
        "log", "info", "debug", "warn", "warning", "error", "fatal", "trace",
        "line", "at", "null", "undefined", "function", "object", "string",
    }
    
    def __init__(self, min_word_length: int = 3, max_word_length: int = 50):
        """
        Initialize TF-IDF scorer.
        
        Args:
            min_word_length: Minimum word length to consider
            max_word_length: Maximum word length (filter out garbage)
        """
        self.min_word_length = min_word_length
        self.max_word_length = max_word_length
        
    def _tokenize(self, text: str) -> List[str]:
        """
        Tokenize text into alphanumeric words.
        
        Args:
            text: Input text (log line)
        
        Returns:
            List of lowercase tokens
        """
        # Extract alphanumeric words (including underscores)
        words = re.findall(r'\b[a-zA-Z0-9_]+\b', text.lower())
        
        # Filter by length and stop words
        tokens = [
            w for w in words
            if self.min_word_length <= len(w) <= self.max_word_length
            and w not in self.STOP_WORDS
        ]
        
        return tokens
    
    def _compute_tf(self, tokens: List[str]) -> Dict[str, float]:
        """
        Compute term frequency (TF) for tokens.
        
        TF(t) = (count of term t in document) / (total terms in document)
        
        Args:
            tokens: List of tokens
        
        Returns:
            Dict mapping term -> TF score
        """
        if not tokens:
            return {}
        
        counts = Counter(tokens)
        total = len(tokens)
        
        return {term: count / total for term, count in counts.items()}
    
    def _compute_idf(self, documents: List[List[str]]) -> Dict[str, float]:
        """
        Compute inverse document frequency (IDF) across documents.
        
        IDF(t) = log(total_docs / docs_containing_t)
        
        Args:
            documents: List of tokenized documents
        
        Returns:
            Dict mapping term -> IDF score
        """
        if not documents:
            return {}
        
        # Count document frequency for each term
        doc_freq = defaultdict(int)
        for doc in documents:
            unique_terms = set(doc)
            for term in unique_terms:
                doc_freq[term] += 1
        
        # Compute IDF
        total_docs = len(documents)
        idf = {}
        
        for term, freq in doc_freq.items():
            idf[term] = log(total_docs / freq)
        
        return idf
    
    def extract_keywords(
        self,
        log_lines: List[str],
        top_k: int = 10,
        min_idf: float = 0.5,
    ) -> List[Tuple[str, float]]:
        """
        Extract top-k keywords from log lines using TF-IDF.
        
        Args:
            log_lines: List of log lines
            top_k: Number of top keywords to return
            min_idf: Minimum IDF threshold (filter common terms)
        
        Returns:
            List of (keyword, tfidf_score) tuples, sorted by score descending
        """
        if not log_lines:
            logger.warning("No log lines provided to TFIDFScorer")
            return []
        
        # Tokenize all documents
        documents = [self._tokenize(line) for line in log_lines]
        
        # Filter empty documents
        documents = [doc for doc in documents if doc]
        
        if not documents:
            logger.warning("No valid tokens extracted from log lines")
            return []
        
        # Compute IDF across all documents
        idf = self._compute_idf(documents)
        
        # Compute TF-IDF for all terms across all documents
        tfidf_scores = defaultdict(float)
        
        for doc in documents:
            tf = self._compute_tf(doc)
            
            for term, tf_score in tf.items():
                idf_score = idf.get(term, 0.0)
                
                # Skip terms with low IDF (too common)
                if idf_score < min_idf:
                    continue
                
                # Accumulate TF-IDF score
                tfidf_score = tf_score * idf_score
                tfidf_scores[term] = max(tfidf_scores[term], tfidf_score)
        
        # Sort by TF-IDF score descending
        ranked = sorted(
            tfidf_scores.items(),
            key=lambda x: x[1],
            reverse=True,
        )
        
        result = ranked[:top_k]
        
        logger.info(
            f"Extracted {len(result)} keywords from {len(log_lines)} log lines "
            f"(tokenized {len(documents)} non-empty docs)"
        )
        
        return result
    
    def score_text(self, text: str, idf: Dict[str, float]) -> float:
        """
        Score a single text using pre-computed IDF.
        
        Useful for scoring query relevance against a corpus.
        
        Args:
            text: Text to score
            idf: Pre-computed IDF dict
        
        Returns:
            TF-IDF score (sum of term scores)
        """
        tokens = self._tokenize(text)
        
        if not tokens:
            return 0.0
        
        tf = self._compute_tf(tokens)
        
        score = sum(
            tf_val * idf.get(term, 0.0)
            for term, tf_val in tf.items()
        )
        
        return score
