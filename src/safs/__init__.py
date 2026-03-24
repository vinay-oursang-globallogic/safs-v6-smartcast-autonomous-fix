"""
SAFS v6.0 - SmartCast Autonomous Fix System
============================================

An AI-powered autonomous bug-fixing system for Vizio SmartCast TVs.

This package integrates four POC projects and implements a complete
pipeline from JIRA ticket intake to validated PR creation.

Key Components:
- intake: JIRA webhook handling & attachment processing
- log_analysis: Quality gate, BugLayerRouter, pattern matching
- symbolication: ASLR correction, ELF lookup, CDP parsing
- retrieval: Multi-host repository adapter & rate limiting
- context: TF-IDF scoring, MinHash dedup, context assembly
- validation: Tri-path validation (QEMU + Playwright + on-device)
- agents: LangGraph orchestration, fix generation, PR creation
- qdrant_collections: Vector database setup & indexing
- symbol_store: Debug symbol storage (MinIO/S3)
- telemetry: Proactive monitoring & regression detection
- companion_lib: Companion library version resolution
"""

__version__ = "6.0.0"
__author__ = "Vizio SAFS Team"
__all__ = [
    "__version__",
    "__author__",
]
