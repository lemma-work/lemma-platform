"""Render per-service environment from config.toml.

Layering (last wins): packaged defaults -> values derived from config
(ports, features, runtime) -> the [<service>.env] override section.

Services talk to each other over the lemma-local-net container network using
DNS aliases (db, redis, supertokens, kreuzberg, agentbox, backend, frontend);
browser-facing URLs use the 127-0-0-1.sslip.io wildcard host + published ports
(see LOCAL_HOST) so apps and the API share one registrable domain.
"""

from __future__ import annotations

from tomlkit import TOMLDocument

from lemma_stack.config import store
from lemma_stack.paths import LocalPaths

NETWORK_NAME = "lemma-local-net"
CONTAINER_PREFIX = "lemma-local"
POSTGRES_VOLUME = "lemma-local-postgres-data"

# Browser-facing host. `127-0-0-1.sslip.io` is wildcard DNS that resolves itself
# and every subdomain to 127.0.0.1, so the backend, frontend, and per-app
# subdomains all share one registrable domain. That makes app subdomains
# same-site with the API, so the session cookie (scoped to LOCAL_COOKIE_DOMAIN)
# flows to apps with SameSite=Lax over plain HTTP — no proxy, hosts file, or
# certs. The dash form keeps the IP in a single DNS label (TLS-friendly).
LOCAL_HOST = "127-0-0-1.sslip.io"
LOCAL_COOKIE_DOMAIN = f".{LOCAL_HOST}"
# Allow the apex (API/frontend) and any app subdomain, on any published port.
LOCAL_CORS_ORIGIN_REGEX = r"^https?://([a-z0-9-]+\.)?127-0-0-1\.sslip\.io(:\d+)?$"

# Container-side mount points under /app/.local (match the backend/agentbox
# image defaults so app config keeps working).
STATE_MOUNT = "/app/.local/lemma"
WORKSPACES_MOUNT = "/app/.local/workspaces"
OBJECT_STORAGE_MOUNT = "/app/.local/object-storage"
FILES_MOUNT = "/app/.local/files"


def frontend_origin(doc: TOMLDocument) -> str:
    return f"http://{LOCAL_HOST}:{store.port(doc, 'frontend')}"


def backend_origin(doc: TOMLDocument) -> str:
    return f"http://{LOCAL_HOST}:{store.port(doc, 'backend')}"


def app_base_domain(doc: TOMLDocument) -> str:
    # Apps are served by the backend, at <slug>.<this>.
    return f"{LOCAL_HOST}:{store.port(doc, 'backend')}"


def agentbox_app_domain(doc: TOMLDocument) -> str:
    return f"{LOCAL_HOST}:{store.port(doc, 'agentbox')}"


def backend_env(doc: TOMLDocument, paths: LocalPaths) -> dict[str, str]:
    kreuzberg_enabled = store.feature(doc, "kreuzberg")
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
        "LOCAL_KREUZBERG_ENABLED": "true" if kreuzberg_enabled else "false",
        "KREUZBERG_URL": "http://kreuzberg:8000" if kreuzberg_enabled else "",
        # agentbox manager
        "AGENTBOX_API_URL": "http://agentbox:8000",
        "AGENTBOX_API_KEY": store.agentbox_api_key(doc),
        # sandboxes share the network; no host.docker.internal rewrite
        "WORKSPACE_CALLBACK_API_URL": "http://backend:8000",
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
        "EMBEDDING_PROVIDER": "local",
        "WEB_SEARCH_PROVIDER": "duckduckgo",
        # local installs have no public URL: receive chat-surface events by
        # polling/socket instead of webhooks (no-ops until tokens are set)
        "ENABLE_TELEGRAM_POLLING_MODE": "true",
        "ENABLE_SLACK_SOCKET_MODE": "true",
    }
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
    }
    env.update(store.env_overrides(doc, "frontend"))
    return env


def agentbox_env(
    doc: TOMLDocument,
    paths: LocalPaths,
    *,
    provider: str,
    runtime_image: str,
    container_socket: str,
) -> dict[str, str]:
    env = {
        "AGENTBOX_API_KEY": store.agentbox_api_key(doc),
        "AGENTBOX_API_URL": "http://agentbox:8000",
        "AGENTBOX_PROVIDER": provider,
        "AGENTBOX_RUNTIME_IMAGE": runtime_image,
        "AGENTBOX_STORAGE_ROOT": WORKSPACES_MOUNT,
        "AGENTBOX_STORAGE_HOST_ROOT": str(paths.workspaces_dir),
        "AGENTBOX_STATE_DB_PATH": f"{STATE_MOUNT}/agentbox-manager/state.db",
        "AGENTBOX_APP_DOMAIN": agentbox_app_domain(doc),
        # sandboxes join the stack network; reachable by DNS, no host ports
        "AGENTBOX_NETWORK": NETWORK_NAME,
        "AGENTBOX_ADD_HOST_GATEWAY": "false",
    }
    if provider == "podman":
        env["CONTAINER_HOST"] = f"unix://{container_socket}"
    env.update(store.env_overrides(doc, "agentbox"))
    return env


def write_env_file(path, env: dict[str, str], header: str) -> None:
    lines = [f"# {header}", "# GENERATED by lemma-stack — edit config.toml instead.", ""]
    lines.extend(f"{key}={value}" for key, value in env.items())
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
