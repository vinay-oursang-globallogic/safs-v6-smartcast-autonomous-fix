"""
Companion Library Module
========================

Companion library version resolution (static + dynamic).

Components:
- version_matrix.py: Static fallback (firmware version → API schema)
- dynamic_resolver.py: Live resolution via vizio-ssh SSH (NEW in v6.0)
"""

from .dynamic_resolver import DynamicCompanionLibResolver
from .version_matrix import CompanionApiSchema, CompanionLibVersionMatrix

__all__ = [
    "CompanionApiSchema",
    "CompanionLibVersionMatrix",
    "DynamicCompanionLibResolver",
]
