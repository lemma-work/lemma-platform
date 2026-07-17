from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    agentbox_api_key: str
    agentbox_api_url: str
    agentbox_app_domain: str | None = None
    agentbox_provider: str = "kubernetes"
    agentbox_namespace: str = "agentbox"
    agentbox_runtime_image: str = "ghcr.io/lemma-work/lemma-agentbox-runtime:latest"
    agentbox_sandbox_image_pull_policy: str = "IfNotPresent"
    agentbox_runtime_port: int = 8080
    agentbox_runtime_class_name: str = "gvisor"
    agentbox_node_selector_pool: str = "sandbox"
    # 250m floor: under node contention a sandbox degrades toward its request,
    # and below ~250m CLI/tool startup times blow up (measured: lemma --help is
    # ~2s at 250m vs 7-9s at 100m, before gVisor overhead).
    agentbox_sandbox_cpu_request: str = "250m"
    agentbox_sandbox_cpu_limit: str = "1000m"
    agentbox_sandbox_memory_request: str = "500Mi"
    agentbox_sandbox_memory_limit: str = "2Gi"
    agentbox_sandbox_ephemeral_request: str = "512Mi"
    agentbox_sandbox_ephemeral_limit: str = "1Gi"
    agentbox_sandbox_ready_timeout_seconds: int = 120
    agentbox_sandbox_app_ready_timeout_seconds: int = 30
    # Upstream timeout for proxied in-sandbox app requests. The default suits
    # short interactive proxying (browser, etc.); a caller that needs longer
    # (e.g. a synchronous function execute that runs for minutes) overrides it
    # per request via the X-Agentbox-Upstream-Timeout header, clamped to the max.
    agentbox_app_proxy_timeout_seconds: float = 60.0
    agentbox_app_proxy_max_timeout_seconds: float = 3700.0
    agentbox_state_db_path: str = "/data/agentbox-manager/state.db"
    agentbox_state_database_url: str | None = None
    agentbox_state_durable_env_keys: str = "LEMMA_BASE_URL"
    # Comma-separated URL-safe base64 32-byte AES keys. The first key seals
    # new routes; remaining keys decrypt routes during rotation.
    agentbox_endpoint_state_keys: str = ""
    # Static, non-secret runtime capacity is copied into each sandbox env and
    # included in desired-generation hashing. Invocation identity/tokens remain
    # request-scoped and are never persisted here.
    agentbox_function_max_concurrency: int = Field(default=8, ge=1, le=128)
    agentbox_function_max_queued: int = Field(default=32, ge=0, le=4096)
    agentbox_session_idle_timeout_seconds: int = 300
    agentbox_sandbox_idle_timeout_seconds: int = 180
    agentbox_cleanup_interval_seconds: int = 30
    agentbox_activity_lease_ttl_seconds: float = 60.0
    agentbox_lifecycle_claim_ttl_seconds: float = 120.0
    agentbox_lifecycle_claim_wait_seconds: float = 30.0
    agentbox_provider_allocation_ttl_seconds: float = 600.0
    agentbox_reconcile_interval_seconds: float = 60.0
    agentbox_orphan_grace_seconds: float = 120.0
    agentbox_suspended_retention_seconds: float = 604800.0
    agentbox_storage_root: str = "/tmp/agentbox-workspaces"
    agentbox_storage_host_root: str | None = None
    agentbox_endpoint_host: str = "127.0.0.1"
    # When set, sandbox containers join this container network instead of
    # publishing host ports; the manager reaches them by container-name DNS.
    agentbox_network: str | None = None
    agentbox_add_host_gateway: bool = True
    agentbox_platform: str | None = None
    agentbox_memory_limit: str | None = None
    agentbox_cpu_limit: str | None = None
    agentbox_e2e_label: bool = False

    @property
    def agentbox_state_durable_env_key_set(self) -> frozenset[str]:
        """Return the canonical allowlist parsed from the CSV setting."""

        from agentbox.state_store.factory import parse_durable_env_keys

        return parse_durable_env_keys(self.agentbox_state_durable_env_keys) | {
            "AGENTBOX_FUNCTION_MAX_CONCURRENCY",
            "AGENTBOX_FUNCTION_MAX_QUEUED",
        }

    @property
    def agentbox_static_runtime_env(self) -> dict[str, str]:
        return {
            "AGENTBOX_FUNCTION_MAX_CONCURRENCY": str(
                self.agentbox_function_max_concurrency
            ),
            "AGENTBOX_FUNCTION_MAX_QUEUED": str(self.agentbox_function_max_queued),
        }


settings = Settings()
