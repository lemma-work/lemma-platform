from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    environment: str = Field(
        default="development",
        validation_alias=AliasChoices(
            "LEMMA_ENVIRONMENT", "AGENTBOX_ENVIRONMENT", "ENVIRONMENT"
        ),
    )
    release_sha: str | None = Field(
        default=None,
        validation_alias=AliasChoices("LEMMA_RELEASE_SHA", "RELEASE_SHA"),
    )
    observability_enabled: bool = False
    otel_sdk_disabled: bool = False
    otel_service_name: str = "lemma-agentbox"
    otel_service_namespace: str | None = None
    otel_exporter_otlp_protocol: str = "grpc"
    otel_exporter_otlp_endpoint: str | None = None
    containerapp_otel_tracing_grpc_endpoint: str | None = None
    otel_exporter_otlp_headers: str | None = None
    otel_exporter_otlp_traces_endpoint: str | None = None
    otel_exporter_otlp_metrics_endpoint: str | None = None
    otel_exporter_otlp_logs_endpoint: str | None = None
    otel_exporter_otlp_traces_protocol: str | None = None
    otel_exporter_otlp_metrics_protocol: str | None = None
    otel_exporter_otlp_logs_protocol: str | None = None
    otel_exporter_otlp_traces_headers: str | None = None
    otel_exporter_otlp_metrics_headers: str | None = None
    otel_exporter_otlp_logs_headers: str | None = None
    otel_traces_exporter: str = "otlp"
    otel_metrics_exporter: str = "none"
    otel_logs_exporter: str = "none"
    otel_signals: str | None = None
    otel_traces_sampler: str = "parentbased_traceidratio"
    otel_traces_sampler_arg: float = Field(default=0.05, ge=0.0, le=1.0)
    otel_propagators: str = "tracecontext"
    otel_metric_export_interval: int = Field(default=60000, ge=1000)

    agentbox_api_key: str
    agentbox_api_url: str
    agentbox_public_url: str | None = None
    agentbox_provider: str = "docker"
    agentbox_state_db_path: str = "/data/agentbox-manager/state.db"
    agentbox_state_database_url: str | None = None
    agentbox_auto_create_schema: bool = False
    agentbox_docker_socket_path: str = "/var/run/docker.sock"
    agentbox_docker_scope: str = "docker:default"
    agentbox_docker_allow_mutable_images: bool = True
    agentbox_docker_private_network: str | None = None
    agentbox_docker_workspace_memory_bytes: int = Field(
        default=2 * 1024 * 1024 * 1024,
        ge=256 * 1024 * 1024,
    )
    agentbox_docker_workspace_nano_cpus: int = Field(
        default=1_000_000_000,
        ge=100_000_000,
    )
    agentbox_docker_function_memory_bytes: int = Field(
        default=2 * 1024 * 1024 * 1024,
        ge=256 * 1024 * 1024,
    )
    agentbox_docker_function_nano_cpus: int = Field(
        default=4_000_000_000,
        ge=100_000_000,
    )
    agentbox_provider_max_active: int = Field(default=32, ge=1, le=10000)
    agentbox_provider_create_rate_per_second: float = Field(default=2.0, gt=0, le=1000)
    agentbox_provider_create_burst: int = Field(default=4, ge=1, le=1000)
    agentbox_provider_interactive_capacity_reserve: int = Field(default=4, ge=0)
    agentbox_provider_latency_capacity_reserve: int = Field(default=4, ge=0)
    agentbox_max_file_transfer_bytes: int = Field(
        default=256 * 1024 * 1024,
        ge=1,
        le=2 * 1024 * 1024 * 1024,
    )
    agentbox_workspace_profile_name: str = "workspace-python-v1"
    agentbox_workspace_profile_digest: str = f"sha256:{'1' * 64}"
    agentbox_workspace_image: str = "agentbox-workspace:dev"
    agentbox_function_profile_name: str = "function-python-v1"
    agentbox_function_profile_digest: str = f"sha256:{'2' * 64}"
    agentbox_function_image: str = "agentbox-function:dev"
    agentbox_runtime_credential_key: str | None = Field(default=None, min_length=32)
    agentbox_e2b_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("E2B_API_KEY", "AGENTBOX_E2B_API_KEY"),
    )
    agentbox_e2b_scope: str = "e2b:default"
    agentbox_e2b_workspace_template: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "E2B_WORKSPACE_TEMPLATE",
            "AGENTBOX_E2B_WORKSPACE_TEMPLATE",
            "E2B_SANDBOX_TEMPLATE",
        ),
    )
    agentbox_e2b_workspace_build_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "E2B_WORKSPACE_TEMPLATE_BUILD_ID",
            "AGENTBOX_E2B_WORKSPACE_BUILD_ID",
        ),
    )
    agentbox_e2b_function_template: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "E2B_FUNCTION_TEMPLATE",
            "AGENTBOX_E2B_FUNCTION_TEMPLATE",
        ),
    )
    agentbox_e2b_function_build_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "E2B_FUNCTION_TEMPLATE_BUILD_ID",
            "AGENTBOX_E2B_FUNCTION_BUILD_ID",
        ),
    )
    agentbox_e2b_function_allow_out: str = Field(
        default="",
        description=(
            "Comma-separated E2B egress hosts/CIDRs allowed for function sandboxes. "
            "Leave empty to deny all function egress."
        ),
    )
    agentbox_e2b_request_timeout_seconds: float = Field(default=20, ge=1, le=120)
    agentbox_workspace_idle_seconds: float = Field(default=300, ge=1)
    agentbox_function_idle_seconds: float = Field(default=300, ge=1)
    agentbox_cleanup_interval_seconds: float = Field(default=30, ge=1)
    agentbox_reconcile_interval_seconds: float = 60.0
    agentbox_reconcile_operation_timeout_seconds: float = Field(
        default=20, ge=1, le=120
    )
    agentbox_ambiguous_create_absence_grace_seconds: float = Field(
        default=30, ge=1, le=600
    )
    agentbox_reconcile_claim_seconds: float = Field(default=30, ge=5, le=300)
    agentbox_workspace_retention_seconds: float = Field(default=604800, ge=1)
    agentbox_add_host_gateway: bool = True

    @property
    def agentbox_e2b_function_allow_out_hosts(self) -> tuple[str, ...]:
        return tuple(
            host.strip()
            for host in self.agentbox_e2b_function_allow_out.split(",")
            if host.strip()
        )


settings = Settings()
