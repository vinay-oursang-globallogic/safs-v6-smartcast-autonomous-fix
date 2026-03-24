"""
Configuration Management
========================

Loads and validates configuration from environment variables and .env file.
"""

from pathlib import Path
from typing import Optional, Dict, Any
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
import os


class SAFSConfig(BaseSettings):
    """SAFS v6.0 Configuration"""

    # === ANTHROPIC API ===
    anthropic_api_key: str = Field(..., env="ANTHROPIC_API_KEY")
    anthropic_base_url: str = Field("https://api.anthropic.com", env="ANTHROPIC_BASE_URL")
    
    # Model selection
    fix_generator_model: str = Field("claude-opus-4", env="SAFS_FIX_GENERATOR_MODEL")
    root_cause_model: str = Field("claude-haiku", env="SAFS_ROOT_CAUSE_MODEL")
    bug_layer_router_model: str = Field("claude-haiku", env="SAFS_BUG_LAYER_ROUTER_MODEL")
    test_generator_model: str = Field("claude-sonnet", env="SAFS_TEST_GENERATOR_MODEL")

    # === JIRA ===
    jira_url: str = Field(..., env="JIRA_URL")
    jira_username: str = Field(..., env="JIRA_USERNAME")
    jira_api_token: str = Field(..., env="JIRA_API_TOKEN")
    jira_project_key: str = Field("SMARTCAST", env="JIRA_PROJECT_KEY")
    jira_webhook_secret: Optional[str] = Field(None, env="JIRA_WEBHOOK_SECRET")

    # === QDRANT ===
    qdrant_host: str = Field("localhost", env="QDRANT_HOST")
    qdrant_port: int = Field(6333, env="QDRANT_PORT")
    qdrant_api_key: Optional[str] = Field(None, env="QDRANT_API_KEY")
    qdrant_https: bool = Field(False, env="QDRANT_HTTPS")
    qdrant_collection_historical_fixes: str = Field(
        "historical_fixes", env="QDRANT_COLLECTION_HISTORICAL_FIXES"
    )
    qdrant_collection_fix_corrections: str = Field(
        "fix_corrections", env="QDRANT_COLLECTION_FIX_CORRECTIONS"
    )

    # === VOYAGE AI ===
    voyage_api_key: str = Field(..., env="VOYAGE_API_KEY")
    voyage_model: str = Field("voyage-code-3", env="VOYAGE_MODEL")

    # === GITHUB ===
    github_token: str = Field(..., env="GITHUB_TOKEN")
    github_org: str = Field("buddytv", env="GITHUB_ORG")

    # === GITLAB (Optional) ===
    gitlab_url: Optional[str] = Field(None, env="GITLAB_URL")
    gitlab_token: Optional[str] = Field(None, env="GITLAB_TOKEN")

    # === BITBUCKET (Optional) ===
    bitbucket_url: Optional[str] = Field(None, env="BITBUCKET_URL")
    bitbucket_username: Optional[str] = Field(None, env="BITBUCKET_USERNAME")
    bitbucket_app_password: Optional[str] = Field(None, env="BITBUCKET_APP_PASSWORD")

    # === POSTGRESQL ===
    postgres_host: str = Field("localhost", env="POSTGRES_HOST")
    postgres_port: int = Field(5432, env="POSTGRES_PORT")
    postgres_db: str = Field("safs", env="POSTGRES_DB")
    postgres_user: str = Field("safs", env="POSTGRES_USER")
    postgres_password: str = Field(..., env="POSTGRES_PASSWORD")

    # === REDIS ===
    redis_host: str = Field("localhost", env="REDIS_HOST")
    redis_port: int = Field(6379, env="REDIS_PORT")
    redis_password: Optional[str] = Field(None, env="REDIS_PASSWORD")
    redis_db: int = Field(0, env="REDIS_DB")

    # === TEMPORAL.IO ===
    temporal_host: str = Field("localhost", env="TEMPORAL_HOST")
    temporal_port: int = Field(7233, env="TEMPORAL_PORT")
    temporal_namespace: str = Field("safs", env="TEMPORAL_NAMESPACE")
    temporal_task_queue: str = Field("safs-pipeline", env="TEMPORAL_TASK_QUEUE")

    # === VIZIO TV DEVICE ===
    vizio_tv_ip: Optional[str] = Field(None, env="VIZIO_TV_IP")
    vizio_tv_ssh_port: int = Field(22, env="VIZIO_TV_SSH_PORT")
    vizio_tv_ssh_user: str = Field("root", env="VIZIO_TV_SSH_USER")
    vizio_tv_ssh_password: Optional[str] = Field(None, env="VIZIO_TV_SSH_PASSWORD")
    vizio_tv_ssh_key_path: Optional[Path] = Field(None, env="VIZIO_TV_SSH_KEY_PATH")
    vizio_scpl_auth_token: Optional[str] = Field(None, env="VIZIO_SCPL_AUTH_TOKEN")
    vizio_loki_tcp_port: int = Field(4242, env="VIZIO_LOKI_TCP_PORT")

    # === MCP SERVER PATHS ===
    vizio_remote_mcp_path: Path = Field(
        Path("../mcp_tv_controller/vizio-mcp/vizio-remote"),
        env="VIZIO_REMOTE_MCP_PATH"
    )
    vizio_ssh_mcp_path: Path = Field(
        Path("../mcp_tv_controller/vizio-mcp/vizio-ssh"),
        env="VIZIO_SSH_MCP_PATH"
    )
    vizio_loki_mcp_path: Path = Field(
        Path("../mcp_tv_controller/vizio-mcp/vizio-loki"),
        env="VIZIO_LOKI_MCP_PATH"
    )

    # === CODE-INDEX-MCP ===
    code_index_mcp_url: str = Field("http://localhost:8080", env="CODE_INDEX_MCP_URL")
    code_index_pvc_path: Path = Field(Path("/mnt/code-index-data"), env="CODE_INDEX_PVC_PATH")

    # === SYMBOL STORE ===
    symbol_store_endpoint: str = Field("http://localhost:9000", env="SYMBOL_STORE_ENDPOINT")
    symbol_store_access_key: str = Field("minioadmin", env="SYMBOL_STORE_ACCESS_KEY")
    symbol_store_secret_key: str = Field("minioadmin", env="SYMBOL_STORE_SECRET_KEY")
    symbol_store_bucket: str = Field("vizio-symbols", env="SYMBOL_STORE_BUCKET")

    # === ARM TOOLCHAIN ===
    arm_legacy_toolchain_path: Path = Field(
        Path("/opt/arm-linux-gnueabi-4.9"),
        env="ARM_LEGACY_TOOLCHAIN_PATH"
    )
    arm_current_toolchain_path: Path = Field(
        Path("/opt/arm-linux-gnueabi-9.3"),
        env="ARM_CURRENT_TOOLCHAIN_PATH"
    )
    qemu_arm_path: Path = Field(Path("/usr/bin/qemu-arm-static"), env="QEMU_ARM_PATH")

    # === LANGFUSE ===
    langfuse_public_key: Optional[str] = Field(None, env="LANGFUSE_PUBLIC_KEY")
    langfuse_secret_key: Optional[str] = Field(None, env="LANGFUSE_SECRET_KEY")
    langfuse_host: str = Field("https://cloud.langfuse.com", env="LANGFUSE_HOST")

    # === PROMETHEUS ===
    prometheus_pushgateway_url: Optional[str] = Field(
        None, env="PROMETHEUS_PUSHGATEWAY_URL"
    )

    # === SAFS PIPELINE ===
    safs_environment: str = Field("development", env="SAFS_ENVIRONMENT")
    safs_log_level: str = Field("INFO", env="SAFS_LOG_LEVEL")
    safs_dry_run: bool = Field(False, env="SAFS_DRY_RUN")
    
    # Rate limiter
    rate_limit_p0p1_budget: int = Field(5, env="SAFS_RATE_LIMIT_P0P1_BUDGET")
    rate_limit_p2p3_budget: int = Field(3, env="SAFS_RATE_LIMIT_P2P3_BUDGET")
    
    # Confidence thresholds
    confidence_auto_pr: float = Field(0.85, env="SAFS_CONFIDENCE_AUTO_PR")
    confidence_pr_with_review: float = Field(0.65, env="SAFS_CONFIDENCE_PR_WITH_REVIEW")
    confidence_analysis_only: float = Field(0.45, env="SAFS_CONFIDENCE_ANALYSIS_ONLY")

    # === REPOSITORY ADAPTER MAPPING ===
    repo_adapter_github_pattern: str = Field(
        "buddytv/*,vizio-public/*",
        env="REPO_ADAPTER_GITHUB_PATTERN"
    )
    repo_adapter_gitlab_pattern: str = Field(
        "vizio-loki/*,vizio-firmware/*",
        env="REPO_ADAPTER_GITLAB_PATTERN"
    )
    repo_adapter_bitbucket_pattern: str = Field(
        "vizio-apps/*",
        env="REPO_ADAPTER_BITBUCKET_PATTERN"
    )

    # === TELEMETRY ===
    proactive_monitor_interval: int = Field(300, env="SAFS_PROACTIVE_MONITOR_INTERVAL")
    regression_monitor_window: int = Field(72, env="SAFS_REGRESSION_MONITOR_WINDOW")
    error_spike_threshold: float = Field(2.0, env="SAFS_ERROR_SPIKE_THRESHOLD")
    min_affected_users: int = Field(50, env="SAFS_MIN_AFFECTED_USERS")

    # === FEATURE FLAGS ===
    enable_on_device_validation: bool = Field(True, env="SAFS_ENABLE_ON_DEVICE_VALIDATION")
    enable_bug_reproduction: bool = Field(True, env="SAFS_ENABLE_BUG_REPRODUCTION")
    enable_llmlingua2_compression: bool = Field(False, env="SAFS_ENABLE_LLMLINGUA2_COMPRESSION")
    enable_proactive_monitoring: bool = Field(True, env="SAFS_ENABLE_PROACTIVE_MONITORING")
    enable_regression_monitoring: bool = Field(True, env="SAFS_ENABLE_REGRESSION_MONITORING")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def postgres_url(self) -> str:
        """Construct PostgreSQL connection URL."""
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_url(self) -> str:
        """Construct Redis connection URL."""
        auth = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @property
    def tv_available(self) -> bool:
        """Check if TV is configured."""
        return self.vizio_tv_ip is not None

    def get_repo_adapter_patterns(self) -> Dict[str, str]:
        """Get repository adapter pattern mapping."""
        return {
            "github": self.repo_adapter_github_pattern,
            "gitlab": self.repo_adapter_gitlab_pattern,
            "bitbucket": self.repo_adapter_bitbucket_pattern,
        }


# Global config instance
_config: Optional[SAFSConfig] = None


def get_config() -> SAFSConfig:
    """Get or create global config instance."""
    global _config
    if _config is None:
        _config = SAFSConfig()
    return _config


def reload_config() -> SAFSConfig:
    """Reload configuration from environment."""
    global _config
    _config = SAFSConfig()
    return _config
