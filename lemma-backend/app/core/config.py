from pathlib import Path
from typing import Literal, Optional

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from app.core.settings_env import dotenv_path


def _default_local_root() -> Path:
    return Path(__file__).resolve().parents[3] / ".local"


def reveal_secret(value: "SecretStr | str | None") -> str | None:
    """Return the plaintext of a secret setting for actual use.

    Secret settings (API keys) are typed ``SecretStr`` so they never leak through
    ``repr()``/logs/tracebacks. Read them through this helper at the point of use.
    It tolerates a plain ``str`` too, so tests that ``monkeypatch`` a setting with
    a bare string keep working.
    """
    if value is None:
        return None
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    return value


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=dotenv_path(),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
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
        default=10,
        description=(
            "Primary SQLAlchemy connection pool size PER PROCESS. Each API or "
            "worker pod opens up to db_pool_size + db_max_overflow connections. "
            "With N replicas total, the ceiling is N × (db_pool_size + "
            "db_max_overflow + datastore_db_pool_size + "
            "datastore_db_max_overflow). This MUST stay under Postgres "
            "max_connections (default 100). Scale down when adding replicas. "
            "Default 10 is safe for 1 API + 1 worker (60 total with defaults). "
            "Standalone dev can set DB_POOL_SIZE=20 DB_MAX_OVERFLOW=30."
        ),
    )
    db_max_overflow: int = Field(
        default=10,
        description=(
            "Overflow connections beyond db_pool_size before checkout blocks. "
            "Default 10 keeps per-process main pool at 20 max."
        ),
    )
    db_pool_timeout_seconds: float = Field(
        default=10.0,
        description=(
            "Seconds to wait for a free pooled DB connection before raising "
            "(SQLAlchemy default is 30s, which surfaces as a long hang)."
        ),
    )
    db_pool_recycle_seconds: int = Field(
        default=300,
        description=(
            "Recycle pooled DB connections after this many seconds to prevent "
            "stale connections from accumulating (SQLAlchemy pool_recycle)."
        ),
    )
    db_idle_in_transaction_timeout_seconds: float = Field(
        default=60.0,
        description=(
            "Postgres idle_in_transaction_session_timeout in seconds. "
            "Automatically aborts transactions that sit idle (not executing "
            "a query) for longer than this, releasing the connection back to "
            "the pool. Set to 0 to disable. Catches the 'session held open "
            "during external I/O' anti-pattern at the database level."
        ),
    )
    datastore_db_pool_size: int = Field(
        default=5,
        description=(
            "Datastore SQLAlchemy connection pool size PER PROCESS. Each API "
            "or worker pod opens up to datastore_db_pool_size + "
            "datastore_db_max_overflow connections to the datastore database. "
            "Scale down when adding replicas. Default 5 keeps per-process "
            "datastore pool at 10 max."
        ),
    )
    datastore_db_max_overflow: int = Field(
        default=5,
        description=(
            "Overflow connections beyond datastore_db_pool_size. "
            "Default 5 keeps per-process datastore pool at 10 max."
        ),
    )
    worker_concurrency: int = Field(
        default=20,
        description=(
            "Maximum concurrent streaq tasks per worker process. Should not "
            "exceed db_pool_size + db_max_overflow (default 20), since each "
            "task that opens a DB session consumes one pooled connection."
        ),
    )
    # --- Thread-offload pool (app.core.concurrency.offload) ---
    # Blocking work (CPU-bound, sync SDKs, KMS) runs in worker threads via
    # run_blocking(). These partition the pool by workload class so one class
    # can't starve another, and the total raises anyio's default (40) to leave
    # headroom for residual un-limited offloads (embedder/reranker).
    offload_total_threads: int = Field(
        default=64,
        description=(
            "Total worker threads for off-loop blocking work (anyio's global "
            "default thread limiter). Raised from the anyio default (40) so the "
            "named sub-limiters below, which sum above 40, have room alongside "
            "un-limited offloads. Env: ``OFFLOAD_TOTAL_THREADS``."
        ),
    )
    offload_cpu_bound_limit: int = Field(
        default=8,
        description=(
            "Max concurrent CPU-bound offloads (markdown chunking, tokenizing, "
            "zip/unzip). Env: ``OFFLOAD_CPU_BOUND_LIMIT``."
        ),
    )
    offload_external_http_limit: int = Field(
        default=24,
        description=(
            "Max concurrent blocking external-HTTP offloads (sync connector SDKs "
            "like Composio, OAuth token exchanges). Sized near worker_concurrency "
            "so connector-heavy runs aren't throttled, while still bounded. Env: "
            "``OFFLOAD_EXTERNAL_HTTP_LIMIT``."
        ),
    )
    offload_crypto_limit: int = Field(
        default=8,
        description=(
            "Max concurrent crypto offloads (KMS wrap/unwrap gRPC). Env: "
            "``OFFLOAD_CRYPTO_LIMIT``."
        ),
    )
    # --- Event-loop watchdog (app.core.observability.loop_watchdog) ---
    loop_lag_watchdog_interval_seconds: float = Field(
        default=0.5,
        description=(
            "How often the watchdog probes event-loop lag and refreshes the "
            "liveness heartbeat. Env: ``LOOP_LAG_WATCHDOG_INTERVAL_SECONDS``."
        ),
    )
    loop_lag_warn_seconds: float = Field(
        default=0.3,
        description=(
            "Log a warning when measured event-loop lag exceeds this. Env: "
            "``LOOP_LAG_WARN_SECONDS``."
        ),
    )
    loop_lag_unhealthy_seconds: float = Field(
        default=5.0,
        description=(
            "Event-loop lag above this marks the process unhealthy: /livez "
            "returns 503 so a Kubernetes liveness probe restarts a wedged loop. "
            "Env: ``LOOP_LAG_UNHEALTHY_SECONDS``."
        ),
    )
    worker_heartbeat_path: str = Field(
        default="/tmp/worker_heartbeat",
        description=(
            "File the loop watchdog rewrites (epoch seconds) each tick. The "
            "worker has no HTTP server, so its Kubernetes liveness probe execs a "
            "freshness check on this file — a wedged loop stops updating it and "
            "the pod is restarted. Set empty to disable heartbeat writes. Env: "
            "``WORKER_HEARTBEAT_PATH``."
        ),
    )
    worker_shutdown_grace_period_seconds: int = Field(
        default=10,
        description=(
            "Seconds the streaq worker waits for in-flight tasks to finish on "
            "SIGTERM/SIGINT before forcing cancellation (streaq grace_period). "
            "Gives an interrupted agent run time to finalize its status in the "
            "DB before the engine is disposed, avoiding runs stuck in RUNNING. "
            "Keep below the orchestrator's termination grace period (e.g. "
            "Kubernetes terminationGracePeriodSeconds, default 30s)."
        ),
    )
    worker_queue_name: str = Field(
        default="default",
        description=(
            "streaq queue (Redis key namespace) this worker process consumes "
            "from. Production uses the shared 'default' queue, which is also the "
            "default enqueue target. Overridable so an isolated worker can own a "
            "dedicated queue and never compete with the shared worker for jobs — "
            "used by the e2e cancellation test, which SIGTERMs its own worker "
            "mid-run and must be the sole consumer of the run it dispatches."
        ),
    )
    postgres_max_connections: int = Field(
        default=100,
        description=(
            "PostgreSQL max_connections setting. Used at startup to warn if "
            "the per-process pool ceiling could exceed the server limit. "
            "Set to the actual value in your Postgres config."
        ),
    )
    redis_url: str = Field(
        default="redis://localhost:6379",
        description="Redis connection URL",
    )
    max_request_body_bytes: int = Field(
        default=220 * 1024 * 1024,
        description="Global ASGI request-body ceiling, enforced while receiving bytes.",
    )
    redis_max_connections: int = Field(
        default=200,
        description="Maximum pooled Redis connections per process",
    )
    desktop_auth_create_limit: int = Field(
        default=100,
        ge=0,
        description=(
            "Maximum desktop auth handoff requests a client IP may create per "
            "rate-limit window. Set to 0 to disable the application-level cap."
        ),
    )
    desktop_auth_create_window_seconds: int = Field(
        default=60,
        ge=1,
        description="Desktop auth handoff creation rate-limit window in seconds.",
    )
    lemma_default_model_type: Literal["openai_compat", "anthropic_compat"] = Field(
        default="openai_compat",
        description="Server-provided Lemma system model profile provider type.",
    )
    lemma_openai_api_key: Optional[SecretStr] = Field(
        default=None,
        description="API key for the server-provided OpenAI-compatible Lemma model profile.",
    )
    lemma_openai_base_url: str = Field(
        default="https://api.openai.com/v1",
        description=(
            "Base URL for the OpenAI-compatible system model profile. Defaults to "
            "OpenAI; point it at any OpenAI-compatible endpoint (Fireworks, a local "
            "server, a gateway) via LEMMA_OPENAI_BASE_URL."
        ),
    )
    lemma_openai_default_model: str = Field(
        default="",
        description=(
            "Default model name for the OpenAI-compatible system model profile. "
            "No built-in default: when LEMMA_OPENAI_API_KEY is set the model(s) "
            "must be provided via LEMMA_OPENAI_MODEL_NAMES / "
            "LEMMA_OPENAI_DEFAULT_MODEL, otherwise the profile build fails loudly."
        ),
    )
    lemma_openai_model_names: str = Field(
        default="",
        description=(
            "Comma-separated model names for the OpenAI-compatible system model "
            "profile. Required (via env) when LEMMA_OPENAI_API_KEY is set; there "
            "is no built-in model default."
        ),
    )
    lemma_openai_vision_model_names: str = Field(
        default="",
        description=(
            "Comma-separated subset of LEMMA_OPENAI_MODEL_NAMES whose models accept "
            "image input. Gates the image-returning tools (view_image): a text-only "
            "model breaks when image content enters its history, so those tools are "
            "withheld unless a model is listed here. The standard OpenAI /models "
            "endpoint does not report modalities, so vision must be declared "
            "explicitly here; leave empty if no configured model supports vision. "
            "(Provider-discovered profiles can additionally auto-detect image input "
            "when the provider advertises it.)"
        ),
    )
    lemma_anthropic_api_key: Optional[SecretStr] = Field(
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
    session_approval_ttl_seconds: int = Field(
        default=3600,
        description=(
            "How long an APPROVE_FOR_SESSION decision authorizes the approved "
            "action type for a workload within one conversation. Set to 0 to "
            "disable session approvals (every destructive action re-prompts)."
        ),
    )
    delegation_revocation_ttl_seconds: int = Field(
        default=3600,
        description=(
            "How long a revoked delegated workload (e.g. a deleted agent/function) "
            "stays blocked. Must be >= the max access-token lifetime so an "
            "in-flight delegated token cannot outlive its revocation. Set to 0 to "
            "disable delegation revocation."
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
        default="/tmp/lemma-emails",
        description="Directory used by filesystem email transport",
    )
    auth_email_deliverability_checks_enabled: bool = Field(
        default=True,
        description="Validate signup email domains with DNS before creating accounts",
    )
    auth_email_verification_required: bool = Field(
        default=True,
        description=(
            "Require email/password users to verify their email before accessing "
            "application APIs"
        ),
    )
    auth_disposable_email_domains_enabled: bool = Field(
        default=True,
        description="Reject domains in the bundled OSS disposable-email list",
    )
    auth_disposable_email_allowlist: list[str] = Field(
        default_factory=list,
        description="Domains that override the bundled disposable-email list",
    )
    auth_abuse_protection_enabled: bool = Field(
        default=True,
        description="Enable Redis-backed limits on SuperTokens and Telegram auth routes",
    )
    auth_altcha_enabled: bool = Field(
        default=False,
        description="Require self-hosted ALTCHA proof-of-work on email-generating auth APIs",
    )
    auth_altcha_hmac_key: Optional[SecretStr] = Field(
        default=None,
        description="HMAC key used to sign self-hosted ALTCHA challenges",
    )
    auth_altcha_max_number: int = Field(
        default=100_000,
        ge=10_000,
        le=2_000_000,
        description="Maximum proof-of-work search space for ALTCHA challenges",
    )
    auth_trusted_proxy_ips: list[str] = Field(
        default_factory=list,
        description="Immediate proxy IPs allowed to supply Forwarded/X-Forwarded-For",
    )
    auth_bounce_webhook_secret: Optional[SecretStr] = Field(
        default=None,
        description="HMAC secret for normalized hard-bounce webhook events",
    )
    telegram_oidc_client_id: Optional[str] = Field(
        default=None,
        description="Telegram Web Login client ID issued by BotFather",
    )
    telegram_oidc_client_secret: Optional[SecretStr] = Field(
        default=None,
        description="Telegram Web Login client secret issued by BotFather",
    )
    telegram_oidc_redirect_uri: Optional[str] = Field(
        default=None,
        description="Registered Telegram OIDC callback URL",
    )
    telegram_oidc_issuer: str = Field(
        default="https://oauth.telegram.org",
        description="Expected Telegram OIDC issuer",
    )
    telegram_oidc_authorization_endpoint: str = Field(
        default="https://oauth.telegram.org/auth",
        description="Telegram OIDC authorization endpoint",
    )
    telegram_oidc_token_endpoint: str = Field(
        default="https://oauth.telegram.org/token",
        description="Telegram OIDC token endpoint",
    )
    telegram_oidc_jwks_uri: str = Field(
        default="https://oauth.telegram.org/.well-known/jwks.json",
        description="Telegram OIDC JSON Web Key Set endpoint",
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
    release_sha: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("LEMMA_RELEASE_SHA", "RELEASE_SHA"),
        description=(
            "Full 40-character source Git SHA whose immutable image digest is "
            "deployed. Emitted as ``service.version`` and ``release.sha`` on every "
            "log line and added to the OpenTelemetry Resource. Required in "
            "production: startup rejects an empty or non-hex value. Env: "
            "``LEMMA_RELEASE_SHA``."
        ),
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
    storage_backend: Literal["auto", "local", "gcs", "s3", "azure"] = Field(
        default="auto",
        description=(
            "Private object storage backend. 'auto' preserves the historical "
            "local-or-GCS selection. Supported explicit cloud adapters: GCS, "
            "Amazon S3 (and S3-compatible endpoints), and Azure Blob Storage."
        ),
    )
    storage_bucket: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "storage_bucket",
            "STORAGE_BUCKET",
            "GCS_STORAGE_BUCKET",
        ),
        description=(
            "Private storage location: a filesystem directory for local, bucket "
            "name for GCS/S3, or container name for Azure. GCS_STORAGE_BUCKET is "
            "accepted as a backward-compatible environment alias."
        ),
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
            "tauri://localhost",
            "http://tauri.localhost",
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

    @model_validator(mode="after")
    def _require_app_base_domain_outside_local(self) -> "Settings":
        # Apps are served by host at `<slug>.<app_base_domain>`. Outside
        # local/testing there is no safe default (the old `apps.lemma.work`
        # default silently mis-served every install), so fail loud at startup
        # rather than route apps at a wrong/cloud domain. Local installs get this
        # from the stack (APP_BASE_DOMAIN); testing leaves it unset on purpose.
        if not self.is_local_mode() and not (self.app_base_domain or "").strip():
            raise ValueError(
                "APP_BASE_DOMAIN must be set in development/production: it is the "
                "base domain apps are served under (e.g. apps.example.com). It has "
                "no safe default outside local/testing."
            )
        return self

    # App serving: apps are served by host, at `<public_slug>.<app_base_domain>`.
    # Locally the stack sets this to a sslip.io wildcard (e.g.
    # 127-0-0-1.sslip.io:8711) that resolves to loopback; in cloud it is the real
    # apps domain behind the ingress. There is intentionally NO cloud default: an
    # empty value disables host-based app routing, and it is REQUIRED outside
    # local/testing (see _require_app_base_domain_outside_local).
    app_base_domain: str = Field(
        default="",
        description=(
            "Base domain under which public apps are served, as "
            "`<public_slug>.<app_base_domain>`. The local stack sets this to the "
            "sslip.io wildcard host (e.g. 127-0-0-1.sslip.io:8711); in cloud it is "
            "the real apps domain behind the ingress. Empty disables host-based "
            "app routing and is rejected at startup in development/production."
        ),
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
    e2e_llm_mode: Literal["real", "mock"] = Field(
        default="real",
        description=(
            "TEST HOOK ONLY. 'mock' swaps the agent's LLM for a deterministic "
            "pydantic-ai FunctionModel (scripted via conversation metadata) so "
            "e2e runs need no real model or API key. Production/dev leave this at "
            "'real'. The e2e fixtures default it to 'mock' (override with E2E_REAL=1)."
        ),
    )
    e2e_mock_llm_latency_ms: int = Field(
        default=0,
        description=(
            "TEST HOOK ONLY. Per-turn delay (ms) the mock LLM sleeps before "
            "streaming, to emulate real model I/O latency. Default 0 (instant). "
            "Set this for load tests so the worker is I/O-bound like production "
            "instead of CPU-bound on an instant mock — otherwise concurrent runs "
            "saturate one core and distort connection/latency measurements."
        ),
    )
    e2e_sandbox_mode: Literal["docker", "fake"] = Field(
        default="docker",
        description=(
            "TEST HOOK ONLY. 'fake' runs workspace/CLI tools against an in-process "
            "subprocess AgentBox instead of the Docker manager, so e2e needs no "
            "Docker image. Production/dev leave this at 'docker'. The e2e fixtures "
            "default it to 'fake' (override with E2E_REAL=1)."
        ),
    )
    e2e_disable_worker_file_autoindex: bool = Field(
        default=False,
        description=(
            "TEST HOOK ONLY. When true, the worker does NOT auto-index uploaded "
            "datastore files (the upload->event->process_datastore_file_task path "
            "is skipped). e2e indexes explicitly in-process via the index_file "
            "helper; auto-indexing every upload would otherwise overwhelm the "
            "single shared Kreuzberg under parallel load. Production leaves False."
        ),
    )
    agentbox_api_url: Optional[str] = Field(
        description="AgentBox manager API base URL used by workspace execution",
        default=None,
    )
    agentbox_api_key: Optional[str] = Field(
        description="Bearer API key for the AgentBox manager", default=None
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
        default=False,
        description=(
            "Enable the independent LLM/OpenInference trace pipeline. It is "
            "disabled by default and never exports through the general OTLP pipeline."
        ),
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
    llm_otel_traces_sampler: str = Field(
        default="traceidratio",
        description="Independent sampler used by the LLM trace pipeline",
    )
    llm_otel_traces_sampler_arg: float = Field(
        default=0.01,
        ge=0.0,
        le=1.0,
        description="Independent LLM trace sampling ratio (default 1%%)",
    )
    observability_enabled: bool = Field(
        default=False,
        description="Enable OpenTelemetry-based observability",
    )
    otel_sdk_disabled: bool = Field(
        default=False,
        description="Standard OpenTelemetry hard-disable switch",
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
    otel_exporter_otlp_traces_endpoint: Optional[str] = Field(default=None)
    otel_exporter_otlp_metrics_endpoint: Optional[str] = Field(default=None)
    otel_exporter_otlp_logs_endpoint: Optional[str] = Field(default=None)
    otel_exporter_otlp_traces_protocol: Optional[str] = Field(default=None)
    otel_exporter_otlp_metrics_protocol: Optional[str] = Field(default=None)
    otel_exporter_otlp_logs_protocol: Optional[str] = Field(default=None)
    otel_exporter_otlp_traces_headers: Optional[str] = Field(default=None)
    otel_exporter_otlp_metrics_headers: Optional[str] = Field(default=None)
    otel_exporter_otlp_logs_headers: Optional[str] = Field(default=None)
    otel_traces_exporter: str = Field(
        default="otlp",
        description="Standard trace exporter selector: otlp or none",
    )
    otel_metrics_exporter: str = Field(
        default="none",
        description="Standard metric exporter selector: otlp or none",
    )
    otel_logs_exporter: str = Field(
        default="none",
        description="Standard log exporter selector: otlp or none",
    )
    otel_signals: Optional[str] = Field(
        default=None,
        description=(
            "Deprecated compatibility selector. Standard OTEL_*_EXPORTER variables "
            "take precedence; missing or empty legacy selection means traces-only."
        ),
    )
    observability_metrics_export_interval_millis: int = Field(
        default=60000,
        ge=1000,
        validation_alias=AliasChoices(
            "OTEL_METRIC_EXPORT_INTERVAL",
            "OBSERVABILITY_METRICS_EXPORT_INTERVAL_MILLIS",
        ),
        description="Metric export interval in milliseconds",
    )
    otel_traces_sampler: str = Field(
        default="parentbased_traceidratio",
        description=(
            "OpenTelemetry trace sampler strategy. Defaults to parent-based 5%% "
            "head sampling so a sampled parent propagates the decision and "
            "independent roots are sampled at the configured ratio. Env: "
            "``OTEL_TRACES_SAMPLER``."
        ),
    )
    otel_traces_sampler_arg: float = Field(
        default=0.05,
        ge=0.0,
        le=1.0,
        description=(
            "Sampling ratio for ratio-based samplers (0.05 = 5%%). Env: "
            "``OTEL_TRACES_SAMPLER_ARG``."
        ),
    )
    lemma_llm_caching_enabled: bool = Field(
        default=False,
        description=(
            "Enable conversation-id-based LLM prompt caching (session affinity). "
            "Set to true when using a provider that supports it (e.g. Fireworks via "
            "lemma-cloud). Activates PromptCachingCapability for OPENAI_COMPATIBLE profiles."
        ),
    )
    embedding_provider: Literal["auto", "local", "openai_compat"] = Field(
        default="auto",
        description=(
            "Embedding backend. 'auto' uses local offline embeddings in "
            "local/testing and openai_compat embeddings elsewhere (when "
            "LEMMA_OPENAI_API_KEY is set). 'openai_compat' uses LEMMA_OPENAI_BASE_URL "
            "+ LEMMA_OPENAI_API_KEY with the model from OPENAI_COMPAT_EMBEDDING_MODEL."
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
    local_embedding_cache_dir: str = Field(
        default="~/.cache/lemma/fastembed",
        description=(
            "Persistent FastEmbed model cache. Model initialization is guarded by "
            "a process-safe lock so concurrent document workers cannot corrupt a "
            "cold download. Env: ``LOCAL_EMBEDDING_CACHE_DIR``."
        ),
    )
    local_embedding_preload: bool = Field(
        default=True,
        description=(
            "Initialize local embeddings during worker startup so model/cache "
            "failures surface before document jobs are accepted."
        ),
    )
    local_embedding_preload_timeout_seconds: float = Field(
        default=900.0,
        description=(
            "Maximum worker-startup time allowed for local model preload, including "
            "a first-run model download."
        ),
    )
    openai_compat_embedding_model: str = Field(
        default="nomic-ai/nomic-embed-text-v1.5",
        description=(
            "Embedding model used when EMBEDDING_PROVIDER=openai_compat. "
            "Served via LEMMA_OPENAI_BASE_URL + LEMMA_OPENAI_API_KEY."
        ),
    )
    reranker_mode: Literal["off", "local", "openai_compat"] = Field(
        default="off",
        description=(
            "Optional second-stage reranker over hybrid retrieval. 'off' is a "
            "no-op (first-stage order kept); 'local' uses a CPU cross-encoder; "
            "'openai_compat' uses the LEMMA_OPENAI_BASE_URL /rerank endpoint "
            "(LEMMA_OPENAI_API_KEY required)."
        ),
    )
    local_reranker_model: str = Field(
        default="BAAI/bge-reranker-v2-m3",
        description="CrossEncoder model used when reranker_mode='local' (Apache-2.0, CPU).",
    )
    openai_compat_reranker_model: str = Field(
        default="qwen3-reranker-8b",
        description="Rerank model used when reranker_mode='openai_compat'.",
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

    def effective_storage_backend(self) -> Literal["local", "gcs", "s3", "azure"]:
        if self.storage_backend != "auto":
            return self.storage_backend
        if self.is_local_mode():
            return "local"
        # Preserve the historical production behavior where a configured bucket
        # implied GCS. New S3/Azure deployments must select their backend.
        return "gcs" if self.storage_bucket else "local"

    def effective_public_storage_backend(self) -> Literal["local", "gcs"]:
        # Public icon storage remains an intentionally separate local/GCS
        # concern. S3/Azure private-object selection must not accidentally turn
        # the existing public-assets path into a differently configured backend.
        if self.storage_backend == "gcs":
            return "gcs"
        if self.storage_backend == "local":
            return "local"
        if self.storage_backend in {"s3", "azure"}:
            return "gcs" if self.public_bucket_name else "local"
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

    def effective_embedding_provider(self) -> Literal["local", "openai_compat"]:
        if self.embedding_provider != "auto":
            return self.embedding_provider
        if self.is_local_mode():
            return "local"
        # Hosted environments embed via the openai_compat endpoint when
        # credentialed; fall back to local offline embeddings when no key is set.
        if self.lemma_openai_api_key:
            return "openai_compat"
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

    def is_telegram_oidc_configured(self) -> bool:
        """Return whether the global Telegram Web Login client is usable."""
        return bool(
            self.telegram_oidc_client_id
            and reveal_secret(self.telegram_oidc_client_secret)
            and self.telegram_oidc_redirect_uri
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
