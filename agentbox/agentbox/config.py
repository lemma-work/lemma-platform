from typing import Literal

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
    agentbox_provider: Literal["kubernetes", "docker", "podman"] = "kubernetes"
    agentbox_namespace: str = "agentbox"
    agentbox_runtime_image: str = (
        "ghcr.io/lemma-work/agentbox-runtime:latest"
    )
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
    agentbox_session_idle_timeout_seconds: int = 300
    agentbox_sandbox_idle_timeout_seconds: int = 300
    agentbox_cleanup_interval_seconds: int = 30
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


settings = Settings()
