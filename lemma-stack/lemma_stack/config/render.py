"""Render per-service environment from config.toml.

Layering (last wins): packaged defaults -> values derived from config
(ports, features, runtime) -> the [<service>.env] override section.

Services talk to each other over the lemma-local-net container network using
DNS aliases (db, redis, supertokens, backend, frontend);
browser-facing URLs use purpose-specific subdomains below ``lemma.localhost``
and published ports so local routing mirrors the production domain model.
"""

from __future__ import annotations

import base64
import hashlib

from tomlkit import TOMLDocument

from lemma_stack.config import store
from lemma_stack.paths import LocalPaths

NETWORK_NAME = "lemma-local-net"
CONTAINER_PREFIX = "lemma-local"
POSTGRES_VOLUME = "lemma-local-postgres-data"

# ``.localhost`` and its subdomains are reserved loopback names. Separate
# frontend/API/app hosts make local behavior match production:
#
#   app.lemma.localhost
#   api.lemma.localhost
#   <slug>.apps.lemma.localhost
#   <sandbox>-<app>.workspaces.lemma.localhost
#
# They remain same-site, so a cookie scoped to ``lemma.localhost`` reaches the
# API and built apps with SameSite=Lax over plain HTTP. No public DNS, hosts-file
# edits, proxy, or development certificate is required.
LOCAL_ROOT_DOMAIN = "lemma.localhost"
LOCAL_FRONTEND_HOST = f"app.{LOCAL_ROOT_DOMAIN}"
LOCAL_BACKEND_HOST = f"api.{LOCAL_ROOT_DOMAIN}"
LOCAL_APPS_DOMAIN = f"apps.{LOCAL_ROOT_DOMAIN}"
LOCAL_WORKSPACES_DOMAIN = f"workspaces.{LOCAL_ROOT_DOMAIN}"
LOCAL_COOKIE_DOMAIN = f".{LOCAL_ROOT_DOMAIN}"
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


def agentbox_app_domain(doc: TOMLDocument) -> str:
    return f"{LOCAL_WORKSPACES_DOMAIN}:{store.port(doc, 'backend')}"


def _agentbox_endpoint_state_key(doc: TOMLDocument) -> str:
    """Derive a stable local-only endpoint key from the generated manager key."""

    digest = hashlib.sha256(store.agentbox_api_key(doc).encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii")


def backend_env(
    doc: TOMLDocument,
    paths: LocalPaths,
    *,
    provider: str,
    runtime_image: str,
    container_socket: str,
) -> dict[str, str]:
    env = {
        "ENVIRONMENT": "local",
        "DEBUG": "true",
        "LOG_LEVEL": "INFO",
        "JSON_LOGS_ENABLED": "true",
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
        "AGENTBOX_API_KEY": store.agentbox_api_key(doc),
        "AGENTBOX_PROVIDER": provider,
        "AGENTBOX_RUNTIME_IMAGE": runtime_image,
        "AGENTBOX_STATE_DATABASE_URL": "postgresql://postgres:postgres@db:5432/agentbox",
        "AGENTBOX_ENDPOINT_STATE_KEYS": _agentbox_endpoint_state_key(doc),
        "AGENTBOX_STORAGE_ROOT": WORKSPACES_MOUNT,
        "AGENTBOX_STORAGE_HOST_ROOT": str(paths.workspaces_dir),
        "AGENTBOX_APP_DOMAIN": agentbox_app_domain(doc),
        "AGENTBOX_NETWORK": NETWORK_NAME,
        "AGENTBOX_ADD_HOST_GATEWAY": "false",
        "AGENTBOX_HOST_ALIAS": "host.lemma.internal",
        "AGENTBOX_REQUIRE_CALLBACK": "true",
        # sandboxes share the network; no host.docker.internal rewrite
        "WORKSPACE_CALLBACK_API_URL": "http://backend:8000",
        "WORKSPACE_CALLBACK_AUTH_URL": "http://frontend:8080/auth",
        "WORKSPACE_CALLBACK_FRONTEND_URL": "http://frontend:8080",
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
        # share the session cookie across the apex API host and app subdomains
        "SESSION_COOKIE_DOMAIN": LOCAL_COOKIE_DOMAIN,
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
        "AUTH_WHATSAPP_MOBILE_VERIFICATION_ENABLED": "false",
        "EMBEDDING_PROVIDER": "local",
        "WEB_SEARCH_PROVIDER": "duckduckgo",
        # local installs have no public URL: receive chat-surface events by
        # polling/socket instead of webhooks (no-ops until tokens are set)
        "ENABLE_TELEGRAM_POLLING_MODE": "true",
        "ENABLE_SLACK_SOCKET_MODE": "true",
    }
    if provider == "podman":
        env["CONTAINER_HOST"] = f"unix://{container_socket}"
    env.update(store.env_overrides(doc, "agentbox"))
    env.update(store.env_overrides(doc, "backend"))
    return env


def host_backend_env(
    doc: TOMLDocument,
    paths: LocalPaths,
    *,
    provider: str,
    runtime_image: str,
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
        runtime_image=runtime_image,
        container_socket="",
    )
    env.pop("PYTHONPATH", None)
    env.pop("CONTAINER_HOST", None)
    env.update(
        {
            "DATABASE_URL": (
                "postgresql+asyncpg://postgres:postgres@127.0.0.1:"
                f"{store.port(doc, 'postgres')}/lemma"
            ),
            "DATASTORE_DATABASE_URL": (
                "postgresql+asyncpg://postgres:postgres@127.0.0.1:"
                f"{store.port(doc, 'postgres')}/lemma_datastore"
            ),
            "REDIS_URL": f"redis://127.0.0.1:{store.port(doc, 'redis')}",
            "SUPERTOKENS_CORE_URL": (
                f"http://127.0.0.1:{store.port(doc, 'supertokens')}"
            ),
            "AGENTBOX_API_URL": f"http://127.0.0.1:{backend_port}/internal/agentbox",
            "AGENTBOX_STATE_DATABASE_URL": (
                "postgresql://postgres:postgres@127.0.0.1:"
                f"{store.port(doc, 'postgres')}/agentbox"
            ),
            "AGENTBOX_STORAGE_ROOT": str(paths.workspaces_dir),
            "AGENTBOX_STORAGE_HOST_ROOT": str(paths.workspaces_dir),
            "AGENTBOX_NETWORK": "",
            "AGENTBOX_ADD_HOST_GATEWAY": "true",
            "WORKSPACE_CALLBACK_API_URL": (
                f"http://host.lemma.internal:{backend_port}"
            ),
            "WORKSPACE_CALLBACK_AUTH_URL": (
                f"http://host.lemma.internal:{frontend_port}/auth"
            ),
            "WORKSPACE_CALLBACK_FRONTEND_URL": (
                f"http://host.lemma.internal:{frontend_port}"
            ),
            "SCHEDULER_API_URL": f"http://127.0.0.1:{backend_port}",
            "LOCAL_OBJECT_STORAGE_ROOT": str(paths.object_storage_dir),
            "LOCAL_FILE_STORAGE_ROOT": str(paths.files_dir),
            "LOCAL_AGENT_RUNTIME_CONFIG_PATH": str(
                paths.state_dir / "agent-runtime.json"
            ),
            "EMAIL_OUTPUT_DIR": str(paths.state_dir / "emails"),
        }
    )
    # User settings retain normal last-wins semantics.
    env.update(store.env_overrides(doc, "agentbox"))
    env.update(store.env_overrides(doc, "backend"))
    return env


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
