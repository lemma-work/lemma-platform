from __future__ import annotations

import json

from pydantic import AliasChoices, BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class RetainedWorkspaceProfile(BaseModel):
    """A previously shipped workspace profile that live sandboxes may still run.

    A workspace keeps its existing profile when the current digest changes, so
    that profile's artifacts must stay resolvable until the workspace is
    recreated from scratch. Without the matching entry here, AgentBox cannot
    validate or reconnect to sandboxes created by the previous release.
    """

    name: str
    digest: str
    image: str | None = None
    e2b_template: str | None = None
    e2b_build_id: str | None = None


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
    agentbox_provider_max_active: int = Field(
        default=90,
        ge=1,
        le=10000,
        description=(
            "Maximum concurrently active provider sandboxes. Must be set from "
            "the provider plan's own concurrency ceiling with headroom: E2B "
            "allows 100 concurrent sandboxes on Pro and 20 on Hobby. One "
            "sandbox serves one user, so this is effectively the number of "
            "simultaneously active users."
        ),
    )
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
    agentbox_workspace_retained_profiles: str = Field(
        default="",
        description=(
            "JSON array of previously shipped workspace profiles that existing "
            "sandboxes may still be running, for example: "
            '[{"name": "workspace-python-v1", "digest": "sha256:<64 hex>", '
            '"image": "agentbox-workspace:1.2.3", '
            '"e2b_template": "<template>", "e2b_build_id": "<build>"}]. '
            "Changing the workspace profile digest no longer replaces sandboxes "
            "that already hold user files, so the previous release's artifacts "
            "must remain resolvable until those workspaces are recreated. Keep "
            "at least the immediately preceding release listed here."
        ),
    )
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
    agentbox_e2b_workspace_timeout_seconds: int = Field(
        default=900,
        ge=600,
        le=24 * 60 * 60,
        description=(
            "Inactivity window after which E2B itself pauses a workspace. "
            "E2B's timeout is a continuous-runtime ceiling rather than an idle "
            "timer, so AgentBox refreshes it on every runtime operation; this "
            "value is therefore the time a workspace survives with no AgentBox "
            "activity at all. It is a backstop for manager failure and must "
            "exceed AgentBox idle cleanup, which pauses first and "
            "deterministically. Note E2B still enforces a hard maximum "
            "continuous runtime per plan (24h on Pro), so a workspace busy for "
            "longer is paused regardless; files survive, processes do not."
        ),
    )
    agentbox_e2b_workspace_timeout_refresh_seconds: int = Field(
        default=60,
        ge=1,
        description=(
            "Minimum interval between provider timeout refreshes for one "
            "workspace. Bounds the extra E2B calls to one per interval instead "
            "of one per runtime operation."
        ),
    )
    agentbox_inventory_sweep_interval_seconds: float = Field(
        default=300,
        ge=10,
        description=(
            "How often to reconcile provider inventory against durable state. "
            "Catches sandboxes nobody owns - a restored database, a renamed "
            "scope, a deleted row - which no other code path can find and "
            "which bill until destroyed."
        ),
    )
    agentbox_inventory_untracked_grace_seconds: float = Field(
        default=900,
        ge=60,
        description=(
            "How long a provider sandbox must look unowned, across separate "
            "sweeps, before it is destroyed. Insurance against clock skew or a "
            "briefly misconfigured scope, because the cost of being wrong is "
            "destroying a live user's sandbox."
        ),
    )
    agentbox_process_lease_seconds: float = Field(
        default=180,
        ge=10,
        description=(
            "How long a running process keeps its sandbox awake per renewal. "
            "Short and repeatedly renewed, so a manager that dies cannot leave "
            "a sandbox pinned - the protection simply expires."
        ),
    )
    agentbox_process_lease_interval_seconds: float = Field(
        default=30,
        ge=1,
        description="How often running processes renew their sandbox lease.",
    )
    agentbox_workspace_idle_seconds: float = Field(default=300, ge=1)
    agentbox_function_idle_seconds: float = Field(default=300, ge=1)
    agentbox_cleanup_interval_seconds: float = Field(default=30, ge=1)
    agentbox_reconcile_interval_seconds: float = 60.0
    agentbox_reconcile_operation_timeout_seconds: float = Field(
        default=20, ge=1, le=120
    )
    agentbox_reserved_create_stale_seconds: float = Field(
        default=30,
        ge=5,
        le=300,
        description=(
            "Age after which a manager reservation with no provider dispatch "
            "is reclaimed safely."
        ),
    )
    agentbox_dispatched_create_stale_seconds: float = Field(
        default=15 * 60,
        ge=30,
        le=24 * 60 * 60,
        description=(
            "Age after which an unfinalized provider create dispatch is treated "
            "as ambiguous and reconciled by exact allocation metadata."
        ),
    )
    agentbox_reconcile_claim_seconds: float = Field(default=30, ge=5, le=300)
    agentbox_workspace_retention_seconds: float = Field(
        default=7 * 24 * 60 * 60,
        ge=1,
        description=(
            "Total workspace inactivity before physical sandbox and storage "
            "are destroyed. The logical workspace remains recreatable."
        ),
    )
    agentbox_add_host_gateway: bool = False
    agentbox_host_alias: str | None = None
    agentbox_local_runtime_cli: str = ""
    agentbox_local_scope: str = "lemma-local:managed"
    agentbox_local_runtime_timeout_seconds: float = Field(default=600, ge=1, le=3600)
    agentbox_local_workspace_memory: str = "2g"
    agentbox_local_workspace_cpus: str = "2"
    agentbox_local_function_memory: str = "2g"
    agentbox_local_function_cpus: str = "4"
    agentbox_local_callback_required: bool = False
    agentbox_local_callback_url: str | None = None
    agentbox_local_callback_health_path: str = "/health"
    agentbox_local_callback_timeout_seconds: float = Field(default=30, ge=1, le=300)

    @model_validator(mode="after")
    def validate_workspace_lifecycle(self) -> Settings:
        if (
            self.agentbox_workspace_retention_seconds
            <= self.agentbox_workspace_idle_seconds
        ):
            raise ValueError(
                "AGENTBOX_WORKSPACE_RETENTION_SECONDS must exceed "
                "AGENTBOX_WORKSPACE_IDLE_SECONDS"
            )
        # Both values now measure inactivity: AgentBox refreshes the E2B
        # timeout on every runtime operation, so the provider's own pause only
        # fires when AgentBox has gone quiet. Keeping it clear of idle cleanup
        # means AgentBox releases first and deterministically, and E2B's stop
        # stays a backstop for manager failure.
        if (
            self.agentbox_provider == "e2b"
            and self.agentbox_e2b_workspace_timeout_seconds
            <= self.agentbox_workspace_idle_seconds
            + 2 * self.agentbox_cleanup_interval_seconds
        ):
            raise ValueError(
                "AGENTBOX_E2B_WORKSPACE_TIMEOUT_SECONDS must exceed idle cleanup "
                "by at least two cleanup intervals"
            )
        if (
            self.agentbox_e2b_workspace_timeout_refresh_seconds
            >= self.agentbox_e2b_workspace_timeout_seconds
        ):
            raise ValueError(
                "AGENTBOX_E2B_WORKSPACE_TIMEOUT_REFRESH_SECONDS must be shorter "
                "than AGENTBOX_E2B_WORKSPACE_TIMEOUT_SECONDS"
            )
        # A lease must outlive its own renewal interval, or a running process
        # would leave its sandbox unprotected between renewals and be released.
        if (
            self.agentbox_process_lease_seconds
            <= self.agentbox_process_lease_interval_seconds
        ):
            raise ValueError(
                "AGENTBOX_PROCESS_LEASE_SECONDS must exceed "
                "AGENTBOX_PROCESS_LEASE_INTERVAL_SECONDS"
            )
        return self

    @property
    def agentbox_retained_workspace_profiles(
        self,
    ) -> tuple[RetainedWorkspaceProfile, ...]:
        """Parse retained workspace profiles, excluding the current digest.

        The current profile is always built separately, and the registry rejects
        duplicate digests, so an operator listing the current release here is
        tolerated rather than fatal.
        """

        raw = self.agentbox_workspace_retained_profiles.strip()
        if not raw:
            return ()
        entries = json.loads(raw)
        if not isinstance(entries, list):
            raise ValueError(
                "AGENTBOX_WORKSPACE_RETAINED_PROFILES must be a JSON array"
            )
        return tuple(
            profile
            for profile in (
                RetainedWorkspaceProfile.model_validate(entry) for entry in entries
            )
            if profile.digest != self.agentbox_workspace_profile_digest
        )

    @property
    def agentbox_e2b_function_allow_out_hosts(self) -> tuple[str, ...]:
        return tuple(
            host.strip()
            for host in self.agentbox_e2b_function_allow_out.split(",")
            if host.strip()
        )


settings = Settings()
