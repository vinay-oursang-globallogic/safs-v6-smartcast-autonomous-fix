"""
Telemetry Module — Phase 13
================

Async post-PR systems for regression detection and proactive monitoring.

Components:
- regression_test_generator.py: Generates tests after PR creation
- regression_correlator.py: 72h post-merge monitoring
- proactive_monitor.py: Proactive spike detection (cron)
- models.py: Data models for telemetry

Extended from jira_auto_fixer/integration_test_generator.py and learning_system.py
"""

from .models import (
    MergedPR,
    TelemetryMetric,
    RegressionAlert,
    ProactiveTicket,
    FixCorrection,
    MistakeSeverity,
)
from .regression_test_generator import RegressionTestGenerator
from .regression_correlator import ProductionRegressionCorrelator
from .proactive_monitor import ProactiveTelemetryMonitor
from .telemetry_client import TelemetryClient, PrometheusTelemetryClient, NoopTelemetryClient

__all__ = [
    # Models
    "MergedPR",
    "TelemetryMetric",
    "RegressionAlert",
    "ProactiveTicket",
    "FixCorrection",
    "MistakeSeverity",
    # Agents
    "RegressionTestGenerator",
    "ProductionRegressionCorrelator",
    "ProactiveTelemetryMonitor",
    # Telemetry clients
    "TelemetryClient",
    "PrometheusTelemetryClient",
    "NoopTelemetryClient",
]
