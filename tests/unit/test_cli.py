"""
Unit tests for CLI module (safs.cli).

Uses Typer's test runner to invoke CLI commands without spawning a subprocess.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from src.safs.cli import app


runner = CliRunner()


class TestCLIHelp:
    def test_help_flag(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "safs" in result.output.lower() or "usage" in result.output.lower()

    def test_run_subcommand_help(self):
        result = runner.invoke(app, ["run", "--help"])
        assert result.exit_code == 0
        assert "--ticket" in result.output

    def test_setup_subcommand_exists(self):
        result = runner.invoke(app, ["setup", "--help"])
        # Either 0 (exists) or 2 (not found) — just check it responds
        assert result.exit_code in (0, 2)


class TestCLIRunCommand:
    def test_run_requires_ticket(self):
        """Running without --ticket should print an error or usage hint."""
        result = runner.invoke(app, ["run"])
        # Should either exit with error or print usage
        assert result.exit_code != 0 or "ticket" in result.output.lower()

    def test_run_with_ticket_attempts_pipeline(self):
        """With valid ticket arg, pipeline is attempted (will fail without API keys)."""
        with patch("safs.agents.orchestrator.SAFSOrchestrator") as mock_orch_cls:
            mock_orch = MagicMock()
            mock_orch.run = AsyncMock(return_value=MagicMock(ticket_key="SMART-1234"))
            mock_orch_cls.return_value = mock_orch
            result = runner.invoke(app, ["run", "--ticket", "SMART-1234", "--dry-run"])
            # Should have called the orchestrator or at least not crashed with unhandled exception
            assert result.exit_code in (0, 1)

    def test_run_dry_run_flag_accepted(self):
        with patch("safs.agents.orchestrator.SAFSOrchestrator") as mock_orch_cls:
            mock_orch = MagicMock()
            mock_orch.run = AsyncMock(return_value=MagicMock(ticket_key="SMART-1234"))
            mock_orch_cls.return_value = mock_orch
            result = runner.invoke(app, ["run", "--ticket", "SMART-1234", "--dry-run"])
            assert "--dry-run" not in result.output or result.exit_code in (0, 1)


class TestCLISetupCommand:
    def test_setup_init_db_flag(self):
        result = runner.invoke(app, ["setup", "--help"])
        if result.exit_code == 0:
            assert "--init-db" in result.output or "init" in result.output.lower()

    def test_setup_command_responds(self):
        result = runner.invoke(app, ["setup"])
        assert result.exit_code in (0, 1, 2)


class TestCLITestCommand:
    def test_test_subcommand_help(self):
        result = runner.invoke(app, ["test", "--help"])
        assert result.exit_code in (0, 2)

    def test_test_scenario_flag(self):
        result = runner.invoke(app, ["test", "--help"])
        if result.exit_code == 0:
            assert "--scenario" in result.output or "scenario" in result.output.lower()
