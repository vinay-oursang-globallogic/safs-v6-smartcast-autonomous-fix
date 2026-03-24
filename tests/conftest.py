"""
Test Utilities & Fixtures
==========================

Shared utilities and fixtures for SAFS tests.
"""

import pytest
from pathlib import Path
from typing import Dict, Any

# Test fixtures directory
FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    """Return path to fixtures directory."""
    return FIXTURES_DIR


@pytest.fixture
def sample_loki_crash() -> Dict[str, Any]:
    """Sample LOKi crash log fixture."""
    return {
        "signal": 11,
        "signal_name": "SIGSEGV",
        "fault_addr": "0x00000000",
        "registers": {
            "pc": "0xb6f12a40",
            "lr": "0xb6f12a20",
            "sp": "0xbeac5d88",
        },
        "backtrace": [
            {"pc": "0xb6f12a40", "function": "???", "library": "libloki_core.so"},
            {"pc": "0xb6f12a20", "function": "???", "library": "libloki_core.so"},
        ],
        "maps": {
            "libloki_core.so": "0xb6f00000",
        },
    }


@pytest.fixture
def sample_jira_ticket() -> Dict[str, Any]:
    """Sample JIRA ticket fixture."""
    return {
        "key": "SMARTCAST-12345",
        "summary": "Netflix crashes on launch - MT5670",
        "description": "Netflix app crashes immediately on launch on MT5670 chipset TVs.",
        "priority": "P1",
        "labels": ["netflix", "crash", "mt5670"],
        "firmware_version": "5.10.22.1",
        "streaming_app": "Netflix",
        "bug_layer": None,  # To be determined by BugLayerRouter
    }


# Mark categories
def pytest_configure(config):
    """Configure pytest with custom markers."""
    config.addinivalue_line(
        "markers", "slow: marks tests as slow (deselect with '-m \"not slow\"')"
    )
    config.addinivalue_line(
        "markers", "integration: marks tests as integration tests"
    )
    config.addinivalue_line(
        "markers", "unit: marks tests as unit tests"
    )
    config.addinivalue_line(
        "markers", "requires_tv: marks tests that require a physical TV"
    )
    config.addinivalue_line(
        "markers", "requires_llm: marks tests that require LLM API access"
    )
