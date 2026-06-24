from pathlib import Path
from typing import Literal, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_local_root() -> Path:
    return Path(__file__).resolve().parents[3] / ".local"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )
    environment: Literal["local", "development", "production", "testing"] = Field(
        default="local",
        description="Environment (development, staging, production)",
    )
    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/lemma",
        description="Database connection URL",
    )
    db_pool_size: int = Field(
        default=20,
        description=(
            "Primary SQLAlchemy connection pool size. The single-process dev app "
            "(standalone_app) runs the HTTP API and the embedded agent worker on "
            "one engine, and agents' in-container CLI calls re-enter the backend, "
            "so the SQLAlchemy default of 5 is far too small and causes "
            "pool-checkout hangs."
        ),
    )
    db_max_overflow: int = Field(
        default=30,
        description="Overflow connections allowed beyond db_pool_size before checkout blocks.",
    )
    db_pool_timeout_seconds: float = Field(
        default=10.0,
        description=(
            "Seconds to wait for a free pooled DB connection before raising "
            "(SQLAlchemy default is 30s, which surfaces as a long hang)."
        ),
    )
    redis_url: str = Field(
        default="redis://localhost:6379",
        description="Redis connection URL",
    )
    redis_max_connections: int = Field(
        default=200,
        description="Maximum pooled Redis connections per process",
    )
    redis_stream_polling_interval_ms: int = Field(
        default=500,
        description=(
            "Polling interval in milliseconds for FastStream Redis stream consumers. "
            "Higher values reduce idle XREAD/XREADGROUP volume at the cost of slightly higher event pickup latency."
        ),
    )
    local_agent_runtime_config_path: str = Field(
        default_factory=lambda: str(
            _default_local_root() / "lemma" / "agent-runtime.json"
        ),
        description=(
            "Local-only file used by the runtime settings API to persist the "
            "system default agent runtime."
        ),
    )
    lemma_default_model_type: Literal["openai_compat", "anthropic_compat"] = Field(
        default="openai_compat",
        description="Server-provided Lemma system model profile provider type.",
    )
    lemma_openai_api_key: Optional[str] = Field(
        default=None,
        description="API key for the server-provided OpenAI-compatible Lemma model profile.",
    )
    lemma_openai_base_url: str = Field(
        default="https://api.fireworks.ai/inference/v1",
        description="Base URL for the server-provided OpenAI-compatible Lemma model profile.",
    )
    lemma_openai_default_model: str = Field(
        default="minimax-m3",
        description="Default public model name for the server-provided OpenAI-compatible Lemma profile.",
    )
    lemma_openai_model_names: str = Field(
        default="minimax-m3,glm-5.2,kimi-k2.7-code,kimi-k2.6,deepseek-v4-pro,deepseek-v4-flash",
        description="Comma-separated public model names for the server-provided OpenAI-compatible Lemma profile.",
    )
    lemma_anthropic_api_key: Optional[str] = Field(
        default=None,
        description="API key for the server-provided Anthropic-compatible Lemma model profile.",
    )
    lemma_anthropic_base_url: str = Field(
        default="https://api.anthropic.com",
        description="Base URL for the server-provided Anthropic-compatible Lemma model profile.",
    )
    lemma_anthropic_default_model: str = Field(
        default="claude-sonnet-4-5",
        description="Default public model name for the server-provided Anthropic-compatible Lemma profile.",
    )
    lemma_anthropic_model_names: str = Field(
        default="claude-sonnet-4-5,claude-haiku-4-5",
        description="Comma-separated public model names for the server-provided Anthropic-compatible Lemma profile.",
    )
    web_search_provider: Literal["auto", "duckduckgo", "searxng", "brave"] = Field(
        default="auto",
        description=(
            "Web search backend. Use duckduckgo for no-key local search, searxng "
            "for a self-hosted instance, brave for Brave Search, or auto."
        ),
    )
    searxng_url: Optional[str] = Field(
        default=None,
        description="SearXNG instance URL used when WEB_SEARCH_PROVIDER=searxng.",
    )
    brave_search_api_key: Optional[str] = Field(
        default=None,
        description="Brave Search API key used when WEB_SEARCH_PROVIDER=brave.",
    )
    datastore_database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/lemma_datastore",
        description="Database URL for datastore data storage (each datastore uses schema=datastore_id)",
    )
    # datastore query/document-processing/kreuzberg/pdf/signed-url config moved to
    # app/modules/datastore/config.py (datastore_database_url stays here — infra).
    user_cache_ttl_seconds: int = Field(
        default=1800,
        description="TTL for cached identity users loaded by id",
    )
    authorization_role_cache_ttl_seconds: int = Field(
        default=300,
        description=(
            "TTL in seconds for cached authorization role snapshots. "
            "Set to 0 to disable the in-process cache."
        ),
    )
    # Google OAuth Settings
    google_client_id: Optional[str] = Field(
        default=None, description="Google OAuth Client ID"
    )
    google_client_secret: Optional[str] = Field(
        default=None, description="Google OAuth Client Secret"
    )
    microsoft_client_id: Optional[str] = Field(
        default=None, description="Microsoft OAuth Client ID"
    )
    microsoft_client_secret: Optional[str] = Field(
        default=None, description="Microsoft OAuth Client Secret"
    )


    # WhatsApp Business API Settings

    # Telegram Bot Settings

    microsoft_tenant_id: Optional[str] = Field(
        default=None,
        description=(
            "Microsoft Entra tenant ID. Defaults to 'common' when unset to allow "
            "both personal and organizational accounts."
        ),
    )
    gcp_project_id: Optional[str] = Field(default=None, description="GCP project ID")
    gcp_location: Optional[str] = Field(default=None, description="GCP location")

    # Secret encryption + signing ("KMS" facility). One system-wide key facility
    # (app/core/crypto) encrypts secrets at rest and signs short-lived tokens.
    secret_key_provider: Literal[
        "auto", "static", "gcp_kms", "gcp_secret_manager", "keychain"
    ] = Field(
        default="auto",
        description=(
            "Backend that supplies/protects encryption keys. 'auto' uses 'static' "
            "unless a GCP KMS key or Secret Manager secret is configured."
        ),
    )
    secret_encryption_key: Optional[str] = Field(
        default=None,
        description=(
            "Primary Fernet key (urlsafe base64) for the static provider. Falls "
            "back to CONNECTOR_ENCRYPTION_KEY, then to a deterministic local seed "
            "in local/testing. Required outside local/testing when no keyset is set."
        ),
    )
    secret_encryption_keyset: Optional[str] = Field(
        default=None,
        description=(
            "Optional JSON array of {kid,key,primary} entries enabling key "
            "rotation for the static provider (primary key encrypts new writes; "
            "retired keys still decrypt until data is re-encrypted forward)."
        ),
    )
    gcp_kms_key_name: Optional[str] = Field(
        default=None,
        description=(
            "Full Cloud KMS crypto-key resource "
            "(projects/…/locations/…/keyRings/…/cryptoKeys/…) used as the KEK for "
            "envelope encryption when secret_key_provider=gcp_kms."
        ),
    )
    gcp_secret_manager_secret_name: Optional[str] = Field(
        default=None,
        description=(
            "Secret Manager secret holding the JSON keyring "
            "(projects/…/secrets/…) used when secret_key_provider=gcp_secret_manager."
        ),
    )
    # Email Settings
    smtp_host: str = Field(default="smtp.gmail.com", description="SMTP server hostname")
    smtp_port: int = Field(default=587, description="SMTP server port")
    smtp_user: Optional[str] = Field(default=None, description="SMTP username")
    smtp_password: Optional[str] = Field(default=None, description="SMTP password")
    smtp_from_email: Optional[str] = Field(
        default=None, description="From email address"
    )
    smtp_from_name: str = Field(default="Lemma", description="From name")
    smtp_use_tls: bool = Field(default=True, description="Use TLS for SMTP")
    email_transport: Literal["smtp", "filesystem"] = Field(
        default="smtp",
        description="Email transport backend",
    )
    email_output_dir: str = Field(
        default="/tmp/gappy-emails",
        description="Directory used by filesystem email transport",
    )

    # Application Settings
    app_name: str = Field(default="Lemma Backend", description="Application name")
    debug: bool = Field(default=True, description="Debug mode")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(
        default="INFO",
        description="Application log level",
    )
    json_logs_enabled: bool = Field(
        default=True,
        description="Emit structured JSON logs instead of console-formatted logs",
    )
    frontend_url: str = Field(
        default="http://localhost:3711", description="Frontend URL for email links"
    )
    auth_frontend_url: str = Field(
        default="http://localhost:4173",
        description="Central auth frontend origin used by the SuperTokens UI",
    )
    auth_website_base_path: str = Field(
        default="/",
        description="Path where the centralized auth UI is rendered",
    )
    api_url: str = Field(
        default="http://localhost:8711", description="API URL for email links"
    )
    cli_api_url: Optional[str] = Field(
        default=None,
        description=(
            "Optional API URL advertised to the Lemma CLI. Local dev can keep "
            "this on HTTP while browser/OAuth URLs use HTTPS."
        ),
    )
    cli_auth_frontend_url: Optional[str] = Field(
        default=None,
        description=(
            "Optional auth frontend URL advertised to the Lemma CLI. Local dev "
            "can keep this distinct from the browser canonical auth URL."
        ),
    )
    scheduler_api_url: str = Field(
        default="http://localhost:8711", description="Scheduler API URL"
    )
    supertokens_core_url: str = Field(
        default="http://localhost:3567", description="Supertokens core URL"
    )
    supertokens_api_base_path: str = Field(
        default="/auth",
        description="SuperTokens API base path relative to the SuperTokens gateway",
    )
    supertokens_api_gateway_path: str = Field(
        default="/st",
        description="SuperTokens gateway path relative to api_url",
    )
    # Kreuzberg + PDF rendering + datastore file-URL config moved to
    # app/modules/datastore/config.py
    # datastore signed-url config moved to app/modules/datastore/config.py
    # Object Storage Settings
    storage_backend: Literal["auto", "local", "gcs"] = Field(
        default="auto",
        description=(
            "Object storage backend. 'auto' uses local storage in local/testing and "
            "GCS when a bucket is configured elsewhere."
        ),
    )
    gcs_storage_bucket: Optional[str] = Field(
        default=None,
        description="Google Cloud Storage bucket name for document storage",
    )
    public_bucket_name: Optional[str] = Field(
        default=None,
        description="Google Cloud Storage bucket name for public assets such as icons",
    )
    local_object_storage_root: str = Field(
        default_factory=lambda: str(_default_local_root() / "object-storage"),
        description="Root directory for local obstore-backed object storage.",
    )
    local_file_storage_root: str = Field(
        default_factory=lambda: str(_default_local_root() / "files"),
        description=(
            "Root directory for local-only file assets such as function, app, "
            "workflow, and public icon files. Used only when the effective storage "
            "backend is local."
        ),
    )
    cors_origins: list[str] = Field(
        default=[
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:3711",
            "http://127.0.0.1:3711",
            "http://localhost:4173",
            "http://127.0.0.1:4173",
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            # Tauri webview origins for the Lemma OS desktop app. The macOS WKWebView serves
# the app from tauri://localhost; Windows/Linux use http://tauri.localhost.
            "tauri://localhost", 
            "http://tauri.localhost"
        ],
        description="Allowed CORS origins",
    )
    cors_origin_regex: Optional[str] = Field(
        default=None,
        description="Optional regex for allowing dynamic frontend origins in CORS",
    )
    session_cookie_domain: Optional[str] = Field(
        default=None,
        description="Optional cookie domain for sharing auth sessions across subdomains",
    )
    session_cookie_secure: Optional[bool] = Field(
        default=None,
        description="Override the secure flag for auth session cookies",
    )
    session_cookie_same_site: Optional[Literal["lax", "none", "strict"]] = Field(
        default=None,
        description="Override SameSite for auth session cookies",
    )

    @field_validator(
        "session_cookie_domain",
        "cli_api_url",
        "cli_auth_frontend_url",
        mode="before",
    )
    @classmethod
    def _blank_optional_string_as_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    # App serving: apps are served by host, at `<public_slug>.<app_base_domain>`.
    # Locally this is a sslip.io wildcard (e.g. 127-0-0-1.sslip.io:8711) that
    # resolves to loopback; in cloud it is the real apps domain behind nginx.
    app_base_domain: str = Field(
        default="apps.asur.work",
        description="Base domain for public app subdomains",
    )
    browser_sdk_path: Optional[str] = Field(
        default=None,
        description=(
            "Override path to the built browser SDK bundle served at "
            "/public/sdk/lemma-client.js. Defaults to the Docker COPY location "
            "or the monorepo lemma-typescript build."
        ),
    )
    browser_ui_path: Optional[str] = Field(
        default=None,
        description=(
            "Override path to the opt-in browser UI bundle (web components) "
            "served at /public/sdk/lemma-ui.js. Defaults to the Docker COPY "
            "location or the monorepo lemma-typescript build."
        ),
    )
    agentbox_api_url: Optional[str] = Field(
        description="AgentBox manager API base URL used by workspace execution",
        default=None
    )
    agentbox_api_key: Optional[str] = Field(
        description="Bearer API key for the AgentBox manager",
        default=None

    )
    workspace_callback_api_url: Optional[str] = Field(
        default=None,
        description=(
            "URL workspace sandboxes use to reach this API (e.g. http://backend:8000 "
            "when sandboxes share a container network); overrides the "
            "localhost->host.docker.internal rewrite"
        ),
    )
    # Composio + connector runtime config moved to app/modules/connectors/config.py
    # Authorization settings
    authz_delegated_tokens_enabled: bool = Field(
        default=True,
        description="Enable delegated workload token claims",
    )

    # DodoPayments + billing model overrides live in the billing module's
    # config.py (now in lemma-cloud).
    llm_otel_enabled: bool = Field(
        default=True,
        description="Enable a separate OTEL exporter for LLM/OpenInference spans",
    )
    llm_otel_exporter_otlp_protocol: str = Field(
        default="grpc",
        description="OTLP protocol for LLM/OpenInference spans: grpc or http/protobuf",
    )
    llm_otel_exporter_otlp_endpoint: Optional[str] = Field(
        default=None,
        description="OTLP endpoint for LLM/OpenInference spans",
    )
    llm_otel_exporter_otlp_headers: Optional[str] = Field(
        default=None,
        description="Comma-separated OTLP headers for LLM/OpenInference spans",
    )
    observability_enabled: bool = Field(
        default=False,
        description="Enable OpenTelemetry-based observability",
    )
    otel_service_name: Optional[str] = Field(
        default=None,
        description="Override OTEL service name",
    )
    otel_service_namespace: Optional[str] = Field(
        default=None,
        description="Optional OTEL service namespace",
    )
    otel_exporter_otlp_protocol: str = Field(
        default="grpc",
        description="OTLP protocol for logs, metrics, and traces: grpc or http/protobuf",
    )
    otel_exporter_otlp_endpoint: Optional[str] = Field(
        default=None,
        description=(
            "Single OTLP endpoint for traces, metrics, and logs. When set (and "
            "observability is enabled), all signals in `otel_signals` are exported here."
        ),
    )
    otel_exporter_otlp_headers: Optional[str] = Field(
        default=None,
        description="Comma-separated OTLP headers applied to all signals (e.g. authorization=<key>)",
    )
    otel_signals: str = Field(
        default="traces,metrics,logs",
        description=(
            "Which OTLP signals to export when an endpoint is set: a comma-separated "
            "subset of traces,metrics,logs. Defaults to all three; set e.g. 'traces' "
            "to export only traces."
        ),
    )
    observability_metrics_export_interval_millis: int = Field(
        default=15000,
        description="Metric export interval for OTEL periodic readers",
    )
    embedding_provider: Literal["auto", "local", "fireworks"] = Field(
        default="auto",
        description=(
            "Embedding backend. 'auto' uses local offline embeddings in "
            "local/testing and Fireworks embeddings elsewhere (when "
            "LEMMA_OPENAI_API_KEY is set)."
        ),
    )
    embedding_dimension: int = Field(
        default=768,
        description="Vector dimension used for datastore embeddings.",
    )
    local_embedding_model: str = Field(
        default="BAAI/bge-base-en-v1.5",
        description="FastEmbed model used for local CPU embeddings.",
    )
    fireworks_embedding_model: str = Field(
        default="nomic-ai/nomic-embed-text-v1.5",
        description=(
            "Fireworks embedding model (served via the OpenAI-compatible "
            "/embeddings endpoint using LEMMA_OPENAI_API_KEY)."
        ),
    )
    reranker_mode: Literal["off", "local", "fireworks"] = Field(
        default="off",
        description=(
            "Optional second-stage reranker over hybrid retrieval. 'off' is a "
            "no-op (first-stage order kept); 'local' uses a CPU cross-encoder; "
            "'fireworks' uses the Fireworks /rerank endpoint (LEMMA_OPENAI_API_KEY)."
        ),
    )
    local_reranker_model: str = Field(
        default="BAAI/bge-reranker-v2-m3",
        description="CrossEncoder model used when reranker_mode='local' (Apache-2.0, CPU).",
    )
    fireworks_reranker_model: str = Field(
        default="accounts/fireworks/models/qwen3-reranker-8b",
        description="Fireworks rerank model used when reranker_mode='fireworks'.",
    )
    reranker_retrieve_n: int = Field(
        default=50,
        description=(
            "First-stage candidate pool size to rerank down from when reranking "
            "is active (retrieve N, rerank to the requested limit)."
        ),
    )

    def is_local_mode(self) -> bool:
        return self.environment in {"local", "testing"}

    def effective_storage_backend(self) -> Literal["local", "gcs"]:
        if self.storage_backend != "auto":
            return self.storage_backend
        if self.is_local_mode():
            return "local"
        return "gcs" if self.gcs_storage_bucket else "local"

    def effective_public_storage_backend(self) -> Literal["local", "gcs"]:
        if self.storage_backend != "auto":
            return self.storage_backend
        if self.is_local_mode():
            return "local"
        return "gcs" if self.public_bucket_name else "local"

    def effective_secret_key_provider(
        self,
    ) -> Literal["static", "gcp_kms", "gcp_secret_manager", "keychain"]:
        if self.secret_key_provider != "auto":
            return self.secret_key_provider
        if self.gcp_kms_key_name:
            return "gcp_kms"
        if self.gcp_secret_manager_secret_name:
            return "gcp_secret_manager"
        return "static"

    def effective_embedding_provider(self) -> Literal["local", "fireworks"]:
        if self.embedding_provider != "auto":
            return self.embedding_provider
        if self.is_local_mode():
            return "local"
        # Hosted environments embed through Fireworks when credentialed; fall back
        # to local offline embeddings when no key is configured.
        if self.lemma_openai_api_key:
            return "fireworks"
        return "local"

    def is_google_oauth_configured(self) -> bool:
        """Check if Google OAuth is properly configured."""
        return all(
            [
                self.google_client_id,
                self.google_client_secret,
            ]
        )

    def is_microsoft_oauth_configured(self) -> bool:
        """Check if Microsoft OAuth is properly configured."""
        return all(
            [
                self.microsoft_client_id,
                self.microsoft_client_secret,
            ]
        )

    def is_teams_bot_configured(self) -> bool:
        """Check if the Teams bot credentials are configured."""
        return bool(self.microsoft_bot_app_id and self.microsoft_bot_app_password)

    def is_email_configured(self) -> bool:
        """Check if email is properly configured."""
        return all(
            [
                self.smtp_host,
                self.smtp_user,
                self.smtp_password,
                self.smtp_from_email,
            ]
        )

    def resolve_browser_sdk_path(self) -> Optional[Path]:
        """Locate the built browser SDK bundle served to no-build apps.

        Order: explicit ``browser_sdk_path`` override → Docker COPY location
        (``/app/browser-sdk/lemma-client.js``) → monorepo build output. Returns
        the first that exists, else ``None``.
        """
        candidates: list[Path] = []
        if self.browser_sdk_path:
            candidates.append(Path(self.browser_sdk_path))
        candidates.append(Path("/app/browser-sdk/lemma-client.js"))
        # config.py → app/core/config.py; parents[3] is the monorepo root.
        repo_root = Path(__file__).resolve().parents[3]
        candidates.append(repo_root / "lemma-typescript" / "public" / "lemma-client.js")
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return None

    def resolve_browser_ui_path(self) -> Optional[Path]:
        """Locate the opt-in browser UI bundle (web components) served to
        no-build apps at ``/public/sdk/lemma-ui.js``.

        Order: explicit ``browser_ui_path`` override → Docker COPY location
        (``/app/browser-sdk/lemma-ui.js``) → monorepo build output. Returns the
        first that exists, else ``None`` (the route 404s when absent).
        """
        candidates: list[Path] = []
        if self.browser_ui_path:
            candidates.append(Path(self.browser_ui_path))
        candidates.append(Path("/app/browser-sdk/lemma-ui.js"))
        repo_root = Path(__file__).resolve().parents[3]
        candidates.append(repo_root / "lemma-typescript" / "public" / "lemma-ui.js")
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return None


# Global settings instance
settings = Settings()
