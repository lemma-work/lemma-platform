"""Render per-service environment from config.toml.

Layering (last wins): packaged defaults -> values derived from config
(ports, features, runtime) -> the [<service>.env] override section.

Services talk to each other over the lemma-local-net container network using
DNS aliases (db, redis, supertokens, backend, frontend). Browser-facing UI and
API URLs use one ``app.lemma.localhost`` hostname on separate published ports
so Safari/WKWebView accepts host-only HttpOnly session cookies.
"""

from __future__ import annotations

import base64
import hmac
import os

from tomlkit import TOMLDocument

from lemma_stack.config import store
from lemma_stack.paths import LocalPaths

NETWORK_NAME = "lemma-local-net"
CONTAINER_PREFIX = "lemma-local"
POSTGRES_VOLUME = "lemma-local-postgres-data"

# ``.localhost`` and its subdomains are reserved loopback names. The main
# frontend and API deliberately share one host:
#
#   app.lemma.localhost:<frontend port>
#   app.lemma.localhost:<backend port>
#   <slug>.apps.lemma.localhost
#   <sandbox>-<app>.workspaces.lemma.localhost
#
# A different API hostname makes its Set-Cookie response third-party to the
# top-level app in Safari/WKWebView, which discards the session. Ports do not
# participate in cookie host matching, so separate host processes still work
# with a host-only cookie. User-authored app hosts never receive that cookie
# directly. No public DNS, hosts-file edits, proxy, or development certificate
# is required.
LOCAL_ROOT_DOMAIN = "lemma.localhost"
LOCAL_FRONTEND_HOST = f"app.{LOCAL_ROOT_DOMAIN}"
LOCAL_BACKEND_HOST = LOCAL_FRONTEND_HOST
LOCAL_APPS_DOMAIN = f"apps.{LOCAL_ROOT_DOMAIN}"
LOCAL_WORKSPACES_DOMAIN = f"workspaces.{LOCAL_ROOT_DOMAIN}"
# Allow every Lemma-local host depth, on any published port.
LOCAL_CORS_ORIGIN_REGEX = r"^https?://([a-z0-9-]+\.)*lemma\.localhost(:\d+)?$"

# Container-side mount points under /app/.local (match the backend/agentbox
# image defaults so app config keeps working).
STATE_MOUNT = "/app/.local/lemma"
WORKSPACES_MOUNT = "/app/.local/workspaces"
OBJECT_STORAGE_MOUNT = "/app/.local/object-storage"
FILES_MOUNT = "/app/.local/files"


def frontend_origin(doc: TOMLDocument) -> str:
    return f"http://{LOCAL_FRONTEND_HOST}:{store.port(doc, 'frontend')}"


def backend_origin(doc: TOMLDocument) -> str:
    return f"http://{LOCAL_BACKEND_HOST}:{store.port(doc, 'backend')}"


def app_base_domain(doc: TOMLDocument) -> str:
    # Apps are served by the backend, at <slug>.<this>.
    return f"{LOCAL_APPS_DOMAIN}:{store.port(doc, 'backend')}"


def _agentbox_runtime_key(doc: TOMLDocument) -> str:
    """Derive a stable local-only runtime credential key."""

    digest = hmac.digest(
        store.agentbox_api_key(doc).encode("utf-8"),
        b"lemma-agentbox-runtime-credential-v1",
        "sha256",
    )
    return base64.urlsafe_b64encode(digest).decode("ascii")


def backend_env(
    doc: TOMLDocument,
    paths: LocalPaths,
    *,
    provider: str,
    workspace_image: str,
    function_image: str,
    container_socket: str,
) -> dict[str, str]:
    adapter_provider = "lemma_local" if provider == "lemma_local" else "docker"
    env = {
        "ENVIRONMENT": "local",
        "DEBUG": "true",
        "LOG_LEVEL": "INFO",
        "JSON_LOGS_ENABLED": "true",
        "LOCAL_HTTP_ACCESS_LOGS_ENABLED": "true",
        "OBSERVABILITY_ENABLED": "false",
        "PYTHONPATH": "/app",
        # infra over the container network
        "DATABASE_URL": "postgresql+asyncpg://postgres:postgres@db:5432/lemma",
        "DATASTORE_DATABASE_URL": "postgresql+asyncpg://postgres:postgres@db:5432/lemma_datastore",
        "REDIS_URL": "redis://redis:6379",
        "SUPERTOKENS_CORE_URL": "http://supertokens:3567",
        "LOCAL_KREUZBERG_ENABLED": "false",
        "KREUZBERG_URL": "",
        "DOCUMENT_PROCESSOR": "markitdown",
        # AgentBox manager is mounted inside this backend process.
        "AGENTBOX_ENVIRONMENT": "local",
        "AGENTBOX_API_URL": "http://backend:8000/internal/agentbox",
        "AGENTBOX_PUBLIC_URL": f"{backend_origin(doc)}/internal/agentbox",
        "AGENTBOX_API_KEY": store.agentbox_api_key(doc),
        "AGENTBOX_PROVIDER": adapter_provider,
        "AGENTBOX_RUNTIME_CREDENTIAL_KEY": _agentbox_runtime_key(doc),
        "AGENTBOX_WORKSPACE_IMAGE": workspace_image,
        "AGENTBOX_FUNCTION_IMAGE": function_image,
        "AGENTBOX_STATE_DATABASE_URL": (
            "postgresql+asyncpg://postgres:postgres@db:5432/agentbox"
        ),
        "AGENTBOX_AUTO_CREATE_SCHEMA": "true",
        "AGENTBOX_DOCKER_SOCKET_PATH": container_socket,
        "AGENTBOX_DOCKER_SCOPE": f"{provider}:local",
        "AGENTBOX_DOCKER_ALLOW_MUTABLE_IMAGES": "false",
        "AGENTBOX_DOCKER_PRIVATE_NETWORK": (
            "" if provider == "lemma_local" else NETWORK_NAME
        ),
        "AGENTBOX_ADD_HOST_GATEWAY": "false",
        "AGENTBOX_HOST_ALIAS": "host.lemma.internal",
        "AGENTBOX_LOCAL_RUNTIME_TIMEOUT_SECONDS": "600",
        # sandboxes share the network; no host.docker.internal rewrite
        "WORKSPACE_CALLBACK_API_URL": "http://backend:8000",
        "WORKSPACE_CALLBACK_AUTH_URL": "http://frontend:8080/auth",
        "WORKSPACE_CALLBACK_FRONTEND_URL": "http://frontend:8080",
        "FUNCTION_RUNTIME_GATEWAY_URL": "http://backend:8000",
        # browser-facing URLs
        "API_URL": backend_origin(doc),
        "FRONTEND_URL": frontend_origin(doc),
        "AUTH_FRONTEND_URL": f"{frontend_origin(doc)}/auth",
        "SCHEDULER_API_URL": "http://backend:8000",
        "AUTH_WEBSITE_BASE_PATH": "/auth",
        "SUPERTOKENS_API_BASE_PATH": "/auth",
        "SUPERTOKENS_API_GATEWAY_PATH": "/st",
        "SESSION_COOKIE_SECURE": "false",
        "SESSION_COOKIE_SAME_SITE": "lax",
        # Blank becomes None in the backend, producing a host-only cookie on
        # app.lemma.localhost. The UI and API share that hostname on separate
        # ports, which works in WKWebView and keeps the session cookie away
        # from user-authored app subdomains.
        "SESSION_COOKIE_DOMAIN": "",
        # apps served by host at <slug>.<app_base_domain>; allow them in CORS
        "APP_BASE_DOMAIN": app_base_domain(doc),
        "CORS_ORIGIN_REGEX": LOCAL_CORS_ORIGIN_REGEX,
        # storage rooted at the mounted ~/.lemma/local/data tree
        "STORAGE_BACKEND": "local",
        "LOCAL_OBJECT_STORAGE_ROOT": OBJECT_STORAGE_MOUNT,
        "LOCAL_FILE_STORAGE_ROOT": WORKSPACES_MOUNT,
        "LOCAL_AGENT_RUNTIME_CONFIG_PATH": f"{STATE_MOUNT}/agent-runtime.json",
        "EMAIL_TRANSPORT": "filesystem",
        "EMAIL_OUTPUT_DIR": f"{STATE_MOUNT}/emails",
        # Local installs do not require SMTP. Keep accounts usable while the
        # filesystem sender remains available for explicitly testing emails.
        "AUTH_EMAIL_VERIFICATION_REQUIRED": "false",
        "AUTH_EMAIL_DELIVERABILITY_CHECKS_ENABLED": "false",
        "AUTH_DISPOSABLE_EMAIL_DOMAINS_ENABLED": "false",
        "AUTH_ABUSE_PROTECTION_ENABLED": "false",
        "AUTH_ALTCHA_ENABLED": "false",
        "DESKTOP_AUTH_CREATE_LIMIT": "0",
        "AUTH_WHATSAPP_MOBILE_VERIFICATION_ENABLED": "false",
        "EMBEDDING_PROVIDER": "local",
        "WEB_SEARCH_PROVIDER": "duckduckgo",
        # local installs have no public URL: receive chat-surface events by
        # polling/socket instead of webhooks (no-ops until tokens are set)
        "ENABLE_TELEGRAM_POLLING_MODE": "true",
        "ENABLE_SLACK_SOCKET_MODE": "true",
    }
    env.update(store.env_overrides(doc, "agentbox"))
    env.update(store.env_overrides(doc, "backend"))
    return env


def host_backend_env(
    doc: TOMLDocument,
    paths: LocalPaths,
    *,
    provider: str,
    workspace_image: str,
    function_image: str,
) -> dict[str, str]:
    """Render the managed native backend environment.

    Infrastructure retains loopback-only forwards during the transition to the
    private VZ/WSL guest. Sandbox-visible URLs are explicit configuration; the
    backend never infers or rewrites hostnames.
    """

    backend_port = store.port(doc, "backend")
    frontend_port = store.port(doc, "frontend")
    env = backend_env(
        doc,
        paths,
        provider=provider,
        workspace_image=workspace_image,
        function_image=function_image,
        container_socket="",
    )
    env.pop("PYTHONPATH", None)
    postgres_password = "postgres"
    redis_url = f"redis://127.0.0.1:{store.port(doc, 'redis')}"
    managed_runtime_cli = ""
    if provider == "lemma_local":
        postgres_password = _managed_secret("LEMMA_MANAGED_POSTGRES_PASSWORD")
        redis_password = _managed_secret("LEMMA_MANAGED_REDIS_PASSWORD")
        managed_runtime_cli = os.environ.get("LEMMA_MANAGED_RUNTIME_CLI", "").strip()
        if not managed_runtime_cli:
            raise ValueError("LEMMA_MANAGED_RUNTIME_CLI is required for lemma_local")
        redis_url = f"redis://:{redis_password}@127.0.0.1:{store.port(doc, 'redis')}"

    env.update(
        {
            "DATABASE_URL": (
                f"postgresql+asyncpg://postgres:{postgres_password}@127.0.0.1:"
                f"{store.port(doc, 'postgres')}/lemma"
            ),
            "DATASTORE_DATABASE_URL": (
                f"postgresql+asyncpg://postgres:{postgres_password}@127.0.0.1:"
                f"{store.port(doc, 'postgres')}/lemma_datastore"
            ),
            "REDIS_URL": redis_url,
            "SUPERTOKENS_CORE_URL": (f"http://127.0.0.1:{store.port(doc, 'supertokens')}"),
            "AGENTBOX_API_URL": f"http://127.0.0.1:{backend_port}/internal/agentbox",
            "AGENTBOX_PUBLIC_URL": (
                f"{backend_origin(doc)}/internal/agentbox"
            ),
            "AGENTBOX_STATE_DATABASE_URL": (
                f"postgresql+asyncpg://postgres:{postgres_password}@127.0.0.1:"
                f"{store.port(doc, 'postgres')}/agentbox"
            ),
            "AGENTBOX_DOCKER_PRIVATE_NETWORK": "",
            "AGENTBOX_ADD_HOST_GATEWAY": "true",
            "WORKSPACE_CALLBACK_API_URL": (f"http://host.lemma.internal:{backend_port}"),
            "WORKSPACE_CALLBACK_AUTH_URL": (f"http://host.lemma.internal:{frontend_port}/auth"),
            "WORKSPACE_CALLBACK_FRONTEND_URL": (f"http://host.lemma.internal:{frontend_port}"),
            "FUNCTION_RUNTIME_GATEWAY_URL": (
                f"http://host.lemma.internal:{backend_port}"
            ),
            "SCHEDULER_API_URL": f"http://127.0.0.1:{backend_port}",
            "LOCAL_OBJECT_STORAGE_ROOT": str(paths.object_storage_dir),
            "LOCAL_FILE_STORAGE_ROOT": str(paths.files_dir),
            "LOCAL_AGENT_RUNTIME_CONFIG_PATH": str(paths.state_dir / "agent-runtime.json"),
            "EMAIL_OUTPUT_DIR": str(paths.state_dir / "emails"),
        }
    )
    if provider == "lemma_local":
        env.update(
            {
                "AGENTBOX_LOCAL_RUNTIME_CLI": managed_runtime_cli,
                "AGENTBOX_LOCAL_SCOPE": "lemma-local:managed",
                "AGENTBOX_ADD_HOST_GATEWAY": "false",
                "AGENTBOX_LOCAL_CALLBACK_REQUIRED": "true",
                "AGENTBOX_LOCAL_CALLBACK_URL": (
                    f"http://host.lemma.internal:{backend_port}"
                ),
            }
        )
    # User settings retain normal last-wins semantics.
    env.update(store.env_overrides(doc, "agentbox"))
    env.update(store.env_overrides(doc, "backend"))
    return env


def _managed_secret(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not 32 <= len(value) <= 128 or not value.isascii() or not value.isalnum():
        raise ValueError(f"{name} must be 32 to 128 ASCII letters or digits")
    return value


def frontend_env(doc: TOMLDocument) -> dict[str, str]:
    env = {
        "NODE_ENV": "production",
        "PORT": "8080",
        "HOSTNAME": "0.0.0.0",
        "NEXT_PUBLIC_API_URL": backend_origin(doc),
        "NEXT_PUBLIC_AUTH_URL": f"{frontend_origin(doc)}/auth",
        "NEXT_PUBLIC_SITE_URL": frontend_origin(doc),
        "NEXT_PUBLIC_AUTH_WEBSITE_BASE_PATH": "/auth",
        "NEXT_PUBLIC_SUPERTOKENS_API_BASE_PATH": "/auth",
        "NEXT_PUBLIC_SUPERTOKENS_API_GATEWAY_PATH": "/st",
        "NEXT_PUBLIC_AUTH_DEFAULT_REDIRECT_URI": f"{frontend_origin(doc)}/",
        "NEXT_PUBLIC_SESSION_TOKEN_DOMAIN": "",
        "NEXT_PUBLIC_AUTH_EMAIL_VERIFICATION_REQUIRED": "false",
    }
    env.update(store.env_overrides(doc, "frontend"))
    return env


def host_frontend_env(doc: TOMLDocument) -> dict[str, str]:
    env = frontend_env(doc)
    env.update(
        {
            "PORT": str(store.port(doc, "frontend")),
            "HOSTNAME": "127.0.0.1",
        }
    )
    env.update(store.env_overrides(doc, "frontend"))
    return env


def write_env_file(path, env: dict[str, str], header: str) -> None:
    lines = [f"# {header}", "# GENERATED by lemma-stack — edit config.toml instead.", ""]
    lines.extend(f"{key}={value}" for key, value in env.items())
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
