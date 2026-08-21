from __future__ import annotations

import json
import os
import tempfile
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit

DEFAULT_BASE_URL = "https://api.lemma.work"
DEFAULT_AUTH_URL = "https://lemma.work/auth"
DEFAULT_CONFIG_PATH = Path.home() / ".lemma" / "config.json"
DEFAULT_SERVER_NAME = "lemma-cloud"
ENV_SERVER_NAME = "env"
LOCAL_SERVER_NAME = "local"


@dataclass(frozen=True)
class CliRuntimeSettings:
    base_url: str = DEFAULT_BASE_URL
    auth_url: str = DEFAULT_AUTH_URL
    token: str | None = None
    refresh_token: str | None = None
    verify_ssl: bool = True
    config_file: Path = DEFAULT_CONFIG_PATH

    @classmethod
    def from_env(cls, config_file: str | None = None) -> "CliRuntimeSettings":
        env_config_file = Path(
            config_file or os.getenv("LEMMA_CONFIG_FILE") or DEFAULT_CONFIG_PATH
        )
        verify_ssl = os.getenv("LEMMA_SSL_NO_VERIFY", "").lower() not in (
            "1",
            "true",
            "yes",
        )
        return cls(
            base_url=os.getenv("LEMMA_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
            auth_url=os.getenv("LEMMA_AUTH_URL", DEFAULT_AUTH_URL).rstrip("/"),
            token=os.getenv("LEMMA_TOKEN") or None,
            refresh_token=os.getenv("LEMMA_REFRESH_TOKEN") or None,
            verify_ssl=verify_ssl,
            config_file=env_config_file,
        )


def config_path_from_arg(config_file: str | None = None) -> Path:
    return CliRuntimeSettings.from_env(config_file).config_file


def load_json(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON: {exc}") from exc


def _migrate_legacy_config(data: dict[str, Any]) -> dict[str, Any]:
    """Upgrade legacy config in place: contexts/active_context -> servers/active_server,
    and the previously-shipped default server ``default`` -> ``DEFAULT_SERVER_NAME``
    (``lemma-cloud``)."""
    # Older rename: contexts -> servers.
    if not isinstance(data.get("servers"), dict) and isinstance(
        data.get("contexts"), dict
    ):
        data["servers"] = data.pop("contexts")
        legacy_active = data.pop("active_context", None)
        if "active_server" not in data and legacy_active:
            data["active_server"] = legacy_active
    data.pop("contexts", None)
    data.pop("active_context", None)
    # This rename: the shipped default server `default` -> `lemma-cloud`. Skip when a
    # `lemma-cloud` already exists so an explicit one is never clobbered; a
    # user-customized `default` is preserved verbatim under the new name.
    servers = data.get("servers")
    if (
        isinstance(servers, dict)
        and "default" in servers
        and DEFAULT_SERVER_NAME not in servers
    ):
        servers[DEFAULT_SERVER_NAME] = servers.pop("default")
        if data.get("active_server") == "default":
            data["active_server"] = DEFAULT_SERVER_NAME
    return data


def _fresh_root_config() -> dict[str, Any]:
    """A brand-new config: a default server pre-pointed at Lemma Cloud and
    selected, so a fresh install talks to https://api.lemma.work out of the box
    (no `servers add` needed — that's only for extra/local servers)."""
    return {
        "active_server": DEFAULT_SERVER_NAME,
        "servers": {
            DEFAULT_SERVER_NAME: {
                "base_url": DEFAULT_BASE_URL,
                "auth_url": DEFAULT_AUTH_URL,
                "defaults": {},
            }
        },
    }


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _fresh_root_config()
    data = load_json(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Invalid config file at {path}: expected object")
    data = _migrate_legacy_config(data)
    if not isinstance(data.get("servers"), dict):
        return _fresh_root_config()
    return data


def _ensure_server_shape(server: dict[str, Any]) -> dict[str, Any]:
    # Keys starting with "_" (_runtime, _sources) are in-memory only.
    next_server = {
        key: value for key, value in server.items() if not key.startswith("_")
    }
    defaults = next_server.get("defaults")
    if defaults is None:
        next_server["defaults"] = {}
    elif not isinstance(defaults, dict):
        raise ValueError("Invalid server config: defaults must be object")
    return next_server


def normalize_server_name(name: str | None) -> str:
    value = (name or "").strip()
    if not value:
        return DEFAULT_SERVER_NAME
    if any(ch.isspace() for ch in value):
        raise ValueError("Server names cannot contain whitespace")
    return value


def should_use_env_server(selected_server: str | None = None) -> bool:
    if selected_server:
        return normalize_server_name(selected_server) == ENV_SERVER_NAME
    return bool(os.getenv("LEMMA_TOKEN"))


def build_env_server_config() -> dict[str, Any]:
    config: dict[str, Any] = {"defaults": {}, "_sources": {}}
    sources = config["_sources"]
    env_map = {
        "base_url": "LEMMA_BASE_URL",
        "auth_url": "LEMMA_AUTH_URL",
        "token": "LEMMA_TOKEN",
        "refresh_token": "LEMMA_REFRESH_TOKEN",
    }
    for key, env_key in env_map.items():
        value = os.getenv(env_key)
        if value:
            config[key] = value.rstrip("/") if key.endswith("_url") else value
            sources[key] = env_key
    defaults = config["defaults"]
    for key, env_key in {
        "org_id": "LEMMA_ORG_ID",
        "pod_id": "LEMMA_POD_ID",
        "conversation_id": "LEMMA_CONVERSATION_ID",
    }.items():
        value = os.getenv(env_key)
        if value:
            defaults[key] = value
            sources[key] = env_key
    return config


def normalize_server_config(
    config: dict[str, Any],
    *,
    selected_server: str | None = None,
) -> tuple[dict[str, Any], str]:
    """Return a server-aware config root plus selected server name."""

    raw = dict(config)
    servers_raw = raw.get("servers")
    if not isinstance(servers_raw, dict):
        raise ValueError("Invalid config file: servers must be object")
    servers = {
        str(name): _ensure_server_shape(server)
        for name, server in servers_raw.items()
        if isinstance(server, dict)
    }

    if DEFAULT_SERVER_NAME not in servers:
        servers[DEFAULT_SERVER_NAME] = _ensure_server_shape({})

    active = normalize_server_name(
        selected_server or os.getenv("LEMMA_SERVER") or raw.get("active_server")
    )
    if active not in servers:
        servers[active] = _ensure_server_shape({})

    root = {
        key: value
        for key, value in raw.items()
        if key not in {"servers", "active_server"}
    }
    # The *stored* selection, not the effective one. `--server` and
    # `LEMMA_SERVER` are per-invocation overrides, and writing either of them
    # here put it into the structure every config save persists — so a command
    # that saved for an unrelated reason (storing a token, switching pod) left
    # the CLI permanently pointed somewhere the user had only visited once.
    # That reached us as a release blocker after a QA run against production.
    #
    # Callers that want the effective server take it from the second return
    # value, which still reflects the override.
    stored = normalize_server_name(raw.get("active_server"))
    root["active_server"] = stored if stored in servers else DEFAULT_SERVER_NAME
    root["servers"] = servers
    return root, active


def get_server_config(root_config: dict[str, Any], server: str) -> dict[str, Any]:
    servers = root_config.setdefault("servers", {})
    if not isinstance(servers, dict):
        raise ValueError("Invalid config file: servers must be object")
    normalized = normalize_server_name(server)
    server_config = servers.setdefault(normalized, {"defaults": {}})
    if not isinstance(server_config, dict):
        raise ValueError(f"Invalid config file: server {normalized!r} must be object")
    servers[normalized] = _ensure_server_shape(server_config)
    if normalized == LOCAL_SERVER_NAME:
        discovered = discover_local_server_config()
        if discovered is not None:
            # Desktop owns this endpoint contract. Refresh it on every load so
            # a safe port rotation cannot leave an older CLI registration
            # pointing at a stale or unrelated listener.
            servers[normalized].update(discovered)
            servers[normalized].pop("_local_discovery_error", None)
        elif not servers[normalized].get("base_url"):
            servers[normalized]["_local_discovery_error"] = (
                "Lemma Desktop local endpoints are unavailable. Start the local "
                "Desktop runtime, then retry with --server local."
            )
    return servers[normalized]


def discover_local_server_config() -> dict[str, Any] | None:
    """Read the Desktop-owned endpoint state without assuming fixed ports."""
    root_override = os.getenv("LEMMA_LOCALD_ROOT")
    if root_override:
        state_path = Path(root_override) / "state.json"
    elif sys.platform == "darwin":
        state_path = (
            Path.home()
            / "Library"
            / "Application Support"
            / "Lemma"
            / "locald"
            / "state.json"
        )
    elif os.name == "nt":
        local_app_data = os.getenv("LOCALAPPDATA")
        if not local_app_data:
            return None
        state_path = Path(local_app_data) / "Lemma" / "locald" / "state.json"
    else:
        state_home = Path(os.getenv("XDG_STATE_HOME") or Path.home() / ".local/state")
        state_path = state_home / "lemma" / "locald" / "state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except OSError, ValueError, TypeError:
        return None
    if not isinstance(state, dict):
        return None
    base_url = state.get("apiUrl")
    frontend_url = state.get("url")
    if not _valid_desktop_endpoint(base_url) or not _valid_desktop_endpoint(
        frontend_url
    ):
        return None
    return {
        "base_url": str(base_url).rstrip("/"),
        "auth_url": f"{str(frontend_url).rstrip('/')}/auth",
        "_sources": {
            "base_url": str(state_path),
            "auth_url": str(state_path),
        },
    }


def _valid_desktop_endpoint(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value)
    return (
        parsed.scheme == "http"
        and parsed.hostname == "app.lemma.localhost"
        and parsed.port is not None
        and parsed.username is None
        and parsed.password is None
        and parsed.path in ("", "/")
        and not parsed.query
        and not parsed.fragment
    )


def put_server_config(
    root_config: dict[str, Any],
    server: str,
    server_config: dict[str, Any],
) -> dict[str, Any]:
    servers = root_config.setdefault("servers", {})
    if not isinstance(servers, dict):
        raise ValueError("Invalid config file: servers must be object")
    normalized = normalize_server_name(server)
    servers[normalized] = _ensure_server_shape(server_config)
    root_config["active_server"] = normalized
    return root_config


def server_names(root_config: dict[str, Any]) -> list[str]:
    servers = root_config.get("servers")
    if not isinstance(servers, dict):
        return []
    return sorted(str(name) for name in servers)


def _strip_private_keys(config: dict[str, Any]) -> dict[str, Any]:
    """Drop in-memory-only keys (_runtime, _sources) before persisting."""
    data = {key: value for key, value in config.items() if not key.startswith("_")}
    servers = data.get("servers")
    if isinstance(servers, dict):
        data["servers"] = {
            name: (
                {key: value for key, value in server.items() if not key.startswith("_")}
                if isinstance(server, dict)
                else server
            )
            for name, server in servers.items()
        }
    return data


def save_config(path: Path, config: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(_strip_private_keys(config), indent=2, sort_keys=True)
    with tempfile.NamedTemporaryFile(
        "w",
        dir=path.parent,
        encoding="utf-8",
        delete=False,
        prefix=f".{path.name}.",
        suffix=".tmp",
    ) as tmp:
        tmp.write(payload)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, path)


@contextmanager
def config_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def mask_token(token: str | None) -> str | None:
    if not token:
        return None
    if len(token) <= 8:
        return "*" * len(token)
    return f"{token[:4]}...{token[-4:]}"


def get_config_default(config: dict[str, Any], key: str) -> str | None:
    defaults = config.get("defaults") or {}
    value = defaults.get(key)
    if isinstance(value, str) and value:
        return value
    return None


def resolve_config_default(
    *,
    explicit: str | None = None,
    env_keys: tuple[str, ...] = (),
    config: dict[str, Any] | None = None,
    config_key: str,
) -> str | None:
    if explicit:
        return explicit
    for env_key in env_keys:
        env_value = os.getenv(env_key)
        if env_value:
            return env_value
    return get_config_default(config or {}, config_key)


def resolve_org_id(
    explicit: str | None = None,
    config: dict[str, Any] | None = None,
) -> str | None:
    return resolve_config_default(
        explicit=explicit,
        env_keys=("LEMMA_ORG_ID",),
        config=config,
        config_key="org_id",
    )


def resolve_pod_id(
    explicit: str | None = None,
    config: dict[str, Any] | None = None,
) -> str | None:
    return resolve_config_default(
        explicit=explicit,
        env_keys=("LEMMA_POD_ID",),
        config=config,
        config_key="pod_id",
    )


def get_auth_session(config: dict[str, Any]) -> dict[str, Any] | None:
    auth = config.get("auth")
    if isinstance(auth, dict):
        return auth
    return None


def get_access_token_from_config(config: dict[str, Any]) -> str | None:
    auth = get_auth_session(config)
    if auth:
        token = auth.get("access_token")
        if isinstance(token, str) and token:
            return token
    token = config.get("token")
    if isinstance(token, str) and token:
        return token
    return None


def get_refresh_token_from_config(config: dict[str, Any]) -> str | None:
    auth = get_auth_session(config)
    if auth:
        token = auth.get("refresh_token")
        if isinstance(token, str) and token:
            return token
    token = config.get("refresh_token")
    if isinstance(token, str) and token:
        return token
    return None


def resolve_base_url(
    explicit: str | None = None,
    config: dict[str, Any] | None = None,
    *,
    use_env: bool = True,
) -> str:
    if (
        config
        and config.get("_local_discovery_error")
        and not (
            explicit
            or config.get("base_url")
            or (os.getenv("LEMMA_BASE_URL") if use_env else None)
        )
    ):
        raise ValueError(str(config["_local_discovery_error"]))
    return (
        explicit
        or (config or {}).get("base_url")
        or (os.getenv("LEMMA_BASE_URL") if use_env else None)
        or DEFAULT_BASE_URL
    )


def resolve_auth_url(
    explicit: str | None = None,
    config: dict[str, Any] | None = None,
    *,
    use_env: bool = True,
) -> str:
    if (
        config
        and config.get("_local_discovery_error")
        and not (
            explicit
            or (config or {}).get("auth_url")
            or (os.getenv("LEMMA_AUTH_URL") if use_env else None)
        )
    ):
        raise ValueError(str(config["_local_discovery_error"]))
    auth = get_auth_session(config or {})
    return (
        explicit
        or (auth or {}).get("auth_url")
        or (config or {}).get("auth_url")
        or (os.getenv("LEMMA_AUTH_URL") if use_env else None)
        or DEFAULT_AUTH_URL
    )


def resolve_token(
    explicit: str | None = None,
    config: dict[str, Any] | None = None,
    *,
    use_env: bool = True,
) -> str:
    settings = CliRuntimeSettings.from_env()
    token = (
        explicit
        or (settings.token if use_env else None)
        or get_access_token_from_config(config or {})
    )
    if not token:
        raise ValueError(
            "Missing token. Pass --token, set LEMMA_TOKEN, or run `lemma auth login`."
        )
    return token


def resolve_verify_ssl(no_verify_ssl: bool = False) -> bool:
    return not no_verify_ssl and CliRuntimeSettings.from_env().verify_ssl


def should_use_managed_auth(explicit_token: str | None = None) -> bool:
    return not explicit_token and not CliRuntimeSettings.from_env().token


def upsert_auth_session(
    config: dict[str, Any], session: dict[str, Any]
) -> dict[str, Any]:
    next_config = dict(config)
    next_config["auth"] = dict(session)
    next_config["token"] = session["access_token"]
    next_config["refresh_token"] = session["refresh_token"]
    return next_config


def clear_auth_session(config: dict[str, Any]) -> dict[str, Any]:
    next_config = dict(config)
    next_config.pop("auth", None)
    next_config.pop("token", None)
    next_config.pop("refresh_token", None)
    return next_config


def resolve_sdk_token(token: str | None = None, config_path: Path | None = None) -> str:
    settings = CliRuntimeSettings.from_env(str(config_path) if config_path else None)
    if token:
        return token
    if settings.token:
        return settings.token
    root_config, server = normalize_server_config(
        load_config(config_path or DEFAULT_CONFIG_PATH)
    )
    config = get_server_config(root_config, server)
    resolved = get_access_token_from_config(config)
    if not resolved:
        raise ValueError(
            "Missing token. Set LEMMA_TOKEN, run `lemma auth login`, or pass token explicitly."
        )
    return resolved


def resolve_sdk_base_url(
    api_url: str | None = None, config_path: Path | None = None
) -> str:
    settings = CliRuntimeSettings.from_env(str(config_path) if config_path else None)
    if api_url:
        return api_url.rstrip("/")
    env_value = os.getenv("LEMMA_BASE_URL")
    if env_value:
        return env_value.rstrip("/")
    root_config, server = normalize_server_config(
        load_config(config_path or DEFAULT_CONFIG_PATH)
    )
    config = get_server_config(root_config, server)
    return (
        (config.get("base_url") if isinstance(config, dict) else None)
        or settings.base_url
    ).rstrip("/")
