"""
Unit tests for SAFSConfig.

Covers:
- Default values
- Environment variable binding
- Optional fields
- Properties (postgres_url, redis_url, tv_available)
- get_config / reload_config helpers
"""

import os
from unittest.mock import patch

import pytest

# Required env vars with minimal valid values
_REQUIRED_ENV = {
    "ANTHROPIC_API_KEY": "test-anthropic-key",
    "JIRA_URL": "https://jira.example.com",
    "JIRA_USERNAME": "testuser",
    "JIRA_API_TOKEN": "test-jira-token",
    "VOYAGE_API_KEY": "test-voyage-key",
    "GITHUB_TOKEN": "ghp_test_token",
    "POSTGRES_PASSWORD": "test-pg-password",
}


def _import_config():
    """Import SAFSConfig, skipping if pydantic-settings not installed."""
    try:
        from safs.config import SAFSConfig
        return SAFSConfig
    except Exception as e:
        pytest.skip(f"SAFSConfig not importable: {e}")


class TestSAFSConfig:
    def _make_config(self, extra: dict = None):
        SAFSConfig = _import_config()
        env = dict(_REQUIRED_ENV)
        if extra:
            env.update(extra)
        with patch.dict(os.environ, env, clear=False):
            return SAFSConfig()

    def test_import(self):
        _import_config()

    def test_default_qdrant_host(self):
        config = self._make_config()
        assert config.qdrant_host == "localhost"

    def test_default_qdrant_port(self):
        config = self._make_config()
        assert config.qdrant_port == 6333

    def test_default_postgres_port(self):
        config = self._make_config()
        assert config.postgres_port == 5432

    def test_default_redis_port(self):
        config = self._make_config()
        assert config.redis_port == 6379

    def test_default_github_org(self):
        config = self._make_config()
        assert config.github_org == "buddytv"

    def test_jira_project_key_default(self):
        config = self._make_config()
        assert config.jira_project_key == "SMARTCAST"

    def test_anthropic_api_key_from_env(self):
        config = self._make_config()
        assert config.anthropic_api_key == "test-anthropic-key"

    def test_github_token_from_env(self):
        config = self._make_config()
        assert config.github_token == "ghp_test_token"

    def test_optional_gitlab_url_defaults_none(self):
        config = self._make_config()
        assert config.gitlab_url is None

    def test_optional_qdrant_api_key_defaults_none(self):
        config = self._make_config()
        assert config.qdrant_api_key is None

    def test_qdrant_https_defaults_false(self):
        config = self._make_config()
        assert config.qdrant_https is False

    def test_voyage_model_default(self):
        config = self._make_config()
        assert config.voyage_model == "voyage-code-3"

    def test_temporal_namespace_default(self):
        config = self._make_config()
        assert config.temporal_namespace == "safs"

    def test_fix_generator_model_default(self):
        config = self._make_config()
        assert "claude" in config.fix_generator_model.lower()

    def test_qdrant_collection_names_set(self):
        config = self._make_config()
        assert config.qdrant_collection_historical_fixes
        assert config.qdrant_collection_fix_corrections

    def test_custom_qdrant_host(self):
        config = self._make_config(extra={"QDRANT_HOST": "qdrant.internal"})
        assert config.qdrant_host == "qdrant.internal"

    def test_custom_jira_project_key(self):
        config = self._make_config(extra={"JIRA_PROJECT_KEY": "MYPROJECT"})
        assert config.jira_project_key == "MYPROJECT"

    def test_bitbucket_optional_fields_none(self):
        config = self._make_config()
        assert config.bitbucket_url is None
        assert config.bitbucket_username is None

    def test_custom_anthropic_key(self):
        config = self._make_config(extra={"ANTHROPIC_API_KEY": "sk-custom-key"})
        assert config.anthropic_api_key == "sk-custom-key"

    def test_feature_flag_enable_on_device_default(self):
        config = self._make_config()
        assert isinstance(config.enable_on_device_validation, bool)

    def test_feature_flag_proactive_monitoring_default(self):
        config = self._make_config()
        assert isinstance(config.enable_proactive_monitoring, bool)

    def test_feature_flag_regression_monitoring_default(self):
        config = self._make_config()
        assert isinstance(config.enable_regression_monitoring, bool)

    def test_confidence_auto_pr_threshold(self):
        config = self._make_config()
        assert 0.0 < config.confidence_auto_pr <= 1.0

    def test_confidence_pr_with_review_threshold(self):
        config = self._make_config()
        assert 0.0 < config.confidence_pr_with_review <= 1.0

    def test_confidence_analysis_only_threshold(self):
        config = self._make_config()
        assert 0.0 < config.confidence_analysis_only <= 1.0

    def test_confidence_thresholds_ordered(self):
        config = self._make_config()
        assert config.confidence_auto_pr > config.confidence_pr_with_review > config.confidence_analysis_only

    def test_postgres_url_property(self):
        config = self._make_config()
        url = config.postgres_url
        assert "postgresql://" in url
        assert config.postgres_host in url
        assert config.postgres_db in url

    def test_redis_url_property_no_password(self):
        config = self._make_config()
        url = config.redis_url
        assert "redis://" in url
        assert str(config.redis_port) in url

    def test_redis_url_property_with_password(self):
        config = self._make_config(extra={"REDIS_PASSWORD": "redis-secret"})
        url = config.redis_url
        assert "redis-secret" in url

    def test_tv_available_false_when_no_ip(self):
        config = self._make_config()
        assert config.tv_available is False

    def test_tv_available_true_when_ip_set(self):
        config = self._make_config(extra={"VIZIO_TV_IP": "192.168.1.100"})
        assert config.tv_available is True

    def test_get_repo_adapter_patterns_returns_dict(self):
        config = self._make_config()
        patterns = config.get_repo_adapter_patterns()
        assert isinstance(patterns, dict)
        assert "github" in patterns
        assert "gitlab" in patterns
        assert "bitbucket" in patterns

    def test_safs_dry_run_default_false(self):
        config = self._make_config()
        assert config.safs_dry_run is False

    def test_safs_dry_run_can_be_enabled(self):
        config = self._make_config(extra={"SAFS_DRY_RUN": "true"})
        assert config.safs_dry_run is True

    def test_custom_gitlab_token(self):
        config = self._make_config(extra={"GITLAB_URL": "https://gitlab.example.com", "GITLAB_TOKEN": "glpat-xyz"})
        assert config.gitlab_url == "https://gitlab.example.com"
        assert config.gitlab_token == "glpat-xyz"

    def test_rate_limit_budgets_positive(self):
        config = self._make_config()
        assert config.rate_limit_p0p1_budget > 0
        assert config.rate_limit_p2p3_budget > 0


class TestGetConfig:
    def test_get_config_returns_instance(self):
        try:
            from safs.config import get_config, reload_config
        except Exception as e:
            pytest.skip(f"config not importable: {e}")

        env = dict(_REQUIRED_ENV)
        with patch.dict(os.environ, env, clear=False):
            # Reset any previously cached config
            import safs.config as cfg_module
            cfg_module._config = None
            config = get_config()
            assert config is not None

    def test_reload_config_creates_new_instance(self):
        try:
            from safs.config import reload_config
            import safs.config as cfg_module
        except Exception as e:
            pytest.skip(f"config not importable: {e}")

        env = dict(_REQUIRED_ENV)
        with patch.dict(os.environ, env, clear=False):
            c1 = reload_config()
            c2 = reload_config()
            assert c1 is not c2

    def test_get_config_cached(self):
        try:
            from safs.config import get_config
            import safs.config as cfg_module
        except Exception as e:
            pytest.skip(f"config not importable: {e}")

        env = dict(_REQUIRED_ENV)
        with patch.dict(os.environ, env, clear=False):
            cfg_module._config = None
            c1 = get_config()
            c2 = get_config()
            assert c1 is c2
