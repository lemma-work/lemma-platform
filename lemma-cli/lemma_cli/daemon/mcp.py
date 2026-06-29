from __future__ import annotations

import json
import os
import re
import shlex
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .catalog import normalize_provider_model_name


LEMMA_MCP_SERVER_NAME = "lemma_tools"
LEMMA_MCP_TOKEN_ENV = "LEMMA_MCP_TOKEN"
LEMMA_MCP_AUTHORIZATION_ENV = "LEMMA_MCP_AUTHORIZATION"
ENABLE_PROVIDER_NATIVE_TOOLS_ENV = "LEMMA_DAEMON_ENABLE_PROVIDER_NATIVE_TOOLS"

DEFAULT_COMMAND_TEMPLATES = {
    "CODEX": "codex app-server {mcp_config_args}",
    "CLAUDE_CODE": (
        "claude -p --model {model} --output-format stream-json "
        "--include-partial-messages --verbose {mcp_config_args}"
    ),
    "OPENCODE": "opencode serve",
}

DEFAULT_CWD_DIRS = {
    "CODEX": "lemma-codex",
    "CLAUDE_CODE": "lemma-claude-code",
    "OPENCODE": "lemma-opencode",
}

# Claude Code's own (non-MCP) tools, passed via --disallowedTools so the agent
# uses the pod's MCP tools instead of the harness's local file/shell tools.
# Each name must match a tool that the installed Claude Code actually exposes:
# an unknown name makes Claude Code print
#   Permission deny rule "<name>" matches no known tool — check for typos.
# on every run. LS, MultiEdit, NotebookRead, and TodoRead were removed in
# Claude Code 2.x (folded into Glob/Bash, Edit, Read, and TodoWrite), so they
# are intentionally absent here — keep this list in sync as Claude Code evolves.
CLAUDE_CODE_NATIVE_TOOLS = (
    "Agent",
    "Bash",
    "Edit",
    "Glob",
    "Grep",
    "NotebookEdit",
    "Read",
    "TodoWrite",
    "Write",
)

OPENCODE_NATIVE_TOOLS = (
    "apply_patch",
    "bash",
    "edit",
    "glob",
    "grep",
    "invalid",
    "list",
    "lsp",
    "question",
    "read",
    "skill",
    "task",
    "todowrite",
    "write",
)


def provider_command(
    *,
    harness_kind: str,
    model_name: str,
    prompt_text: str,
    mcp: dict[str, Any],
    session_id: str | None = None,
) -> list[str]:
    template = provider_command_template(harness_kind)
    mcp_config_args = provider_mcp_command_args(harness_kind=harness_kind, mcp=mcp)
    # Rewrite bare aliases (e.g. Claude Code's "sonnet") to the standard-context
    # model id before they reach the CLI, so profiles saved with the raw alias
    # don't opt into the paid 1M-context variant.
    model_name = normalize_provider_model_name(harness_kind, model_name)
    values = {
        "model": shlex.quote(model_name),
        "prompt": shlex.quote(prompt_text),
        "mcp_url": shlex.quote(str(mcp.get("url") or "")),
        "mcp_server_name": shlex.quote(mcp_server_name(mcp)),
        "mcp_config_args": " ".join(shlex.quote(arg) for arg in mcp_config_args),
    }
    try:
        rendered = template.format(**values)
    except KeyError as exc:
        raise RuntimeError(f"Unknown provider command placeholder: {exc}") from exc
    command = shlex.split(rendered)
    if harness_kind == "CLAUDE_CODE" and session_id:
        command.extend(["--resume", session_id])
    return command


def provider_command_template(harness_kind: str) -> str:
    env_name = f"LEMMA_DAEMON_{harness_kind}_COMMAND"
    return os.getenv(env_name) or DEFAULT_COMMAND_TEMPLATES.get(harness_kind, "")


def provider_mcp_command_args(*, harness_kind: str, mcp: dict[str, Any]) -> list[str]:
    if not has_usable_mcp(mcp):
        return []
    if harness_kind == "CODEX":
        return _codex_mcp_args(mcp)
    if harness_kind == "CLAUDE_CODE":
        return _claude_mcp_args(mcp)
    return []


def provider_environment(*, harness_kind: str, mcp: dict[str, Any]) -> dict[str, str]:
    env = os.environ.copy()
    if not has_usable_mcp(mcp):
        return env
    if harness_kind == "CODEX":
        env.update(_mcp_auth_env(mcp))
    if harness_kind == "OPENCODE":
        env["XDG_DATA_HOME"] = _opencode_data_home()
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps(
            merged_opencode_config(env.get("OPENCODE_CONFIG_CONTENT"), mcp),
            separators=(",", ":"),
        )
    return env


def provider_cwd(harness_kind: str) -> Path:
    configured = _configured_provider_cwd(harness_kind)
    if configured is not None:
        cwd = configured
    else:
        cwd = _default_provider_cwd(harness_kind)
    cwd.mkdir(parents=True, exist_ok=True)
    return cwd


def provider_cwd_for_run(harness_kind: str, mcp: dict[str, Any]) -> Path:
    configured = _configured_provider_cwd(harness_kind)
    if configured is not None:
        cwd = configured
    else:
        cwd = _conversation_provider_cwd(harness_kind, mcp)
    cwd.mkdir(parents=True, exist_ok=True)
    return cwd


def has_usable_mcp(mcp: dict[str, Any]) -> bool:
    return bool(mcp.get("url") and (mcp.get("authorization") or mcp.get("token")))


def mcp_server_name(mcp: dict[str, Any]) -> str:
    return str(mcp.get("server_name") or LEMMA_MCP_SERVER_NAME)


def mcp_authorization(mcp: dict[str, Any]) -> str:
    authorization = str(mcp.get("authorization") or "")
    if authorization:
        return authorization
    token = str(mcp.get("token") or "")
    return f"Bearer {token}" if token else ""


def mcp_url(mcp: dict[str, Any]) -> str:
    value = str(mcp.get("url") or "").strip()
    if not value:
        raise RuntimeError("Daemon MCP payload is missing url")
    return value


def mcp_conversation_id(mcp: dict[str, Any]) -> str:
    value = str(mcp.get("conversation_id") or "").strip()
    if not value:
        raise RuntimeError("Daemon MCP payload is missing conversation_id")
    return value


def mcp_tool_names(mcp: dict[str, Any]) -> list[str]:
    raw = mcp.get("tool_names")
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if str(item)]


def payload_with_reachable_mcp_urls(payload: dict[str, Any], *, base_url: str) -> dict[str, Any]:
    return _rewrite_upstream_mcp_urls(payload, base_url=base_url)


def looks_like_lemma_mcp_payload(payload: object) -> bool:
    if isinstance(payload, dict):
        server_name = (
            payload.get("serverName")
            or payload.get("server_name")
            or payload.get("server")
            or payload.get("mcp_server")
            or payload.get("mcpServer")
        )
        if server_name == LEMMA_MCP_SERVER_NAME:
            return True
        for key in ("toolName", "tool_name", "tool", "name"):
            value = payload.get(key)
            if isinstance(value, str) and _is_provider_scoped_lemma_tool_name(value):
                return True
            if isinstance(value, dict) and looks_like_lemma_mcp_payload(value):
                return True
        return any(looks_like_lemma_mcp_payload(value) for value in payload.values())
    if isinstance(payload, list):
        return any(looks_like_lemma_mcp_payload(item) for item in payload)
    return False


def provider_native_tools_enabled() -> bool:
    return str(os.getenv(ENABLE_PROVIDER_NATIVE_TOOLS_ENV, "")).strip().lower() in {
        "1", "true", "yes", "on",
    }


def merged_opencode_config(raw_config: str | None, mcp: dict[str, Any]) -> dict[str, Any]:
    config: dict[str, Any] = {}
    if raw_config:
        try:
            parsed = json.loads(raw_config)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            config.update(parsed)
    server_name = mcp_server_name(mcp)
    tools = {
        name: True
        for tool_name in mcp_tool_names(mcp)
        for name in (tool_name, f"{server_name}_{tool_name}")
    }
    permission: dict[str, str] = {}
    if not provider_native_tools_enabled():
        tools.update({name: False for name in OPENCODE_NATIVE_TOOLS})
        permission.update({name: "deny" for name in OPENCODE_NATIVE_TOOLS})
    if has_usable_mcp(mcp):
        _deep_merge(
            config,
            {
                "mcp": {
                    server_name: {
                        "type": "remote",
                        "url": str(mcp.get("url") or ""),
                        "headers": {"Authorization": mcp_authorization(mcp)},
                        "enabled": True,
                        "oauth": False,
                    }
                },
                "tools": tools,
            },
        )
    if permission:
        _deep_merge(config, {"permission": permission})
    return config


def mcp_tool_timeout_sec() -> int:
    """MCP tool-call timeout (seconds), overridable for long-running tools."""
    raw = os.getenv("LEMMA_DAEMON_MCP_TOOL_TIMEOUT_SEC")
    if raw:
        try:
            value = int(raw)
            if value > 0:
                return value
        except ValueError:
            pass
    return 300


def _codex_mcp_args(mcp: dict[str, Any]) -> list[str]:
    tool_names = mcp_tool_names(mcp)
    mcp_config: dict[str, Any] = {
        "enabled": True,
        "required": True,
        "url": str(mcp.get("url") or ""),
        "env_http_headers": {
            "Authorization": LEMMA_MCP_AUTHORIZATION_ENV,
        },
        "startup_timeout_sec": 10,
        "tool_timeout_sec": mcp_tool_timeout_sec(),
        "enabled_tools": tool_names,
        "disabled_tools": [],
        "default_tools_approval_mode": "approve",
        "tools": {name: {"approval_mode": "approve"} for name in tool_names},
    }
    args = [
        "-c",
        f"mcp_servers.{mcp_server_name(mcp)}={_toml_value(mcp_config)}",
    ]
    if not provider_native_tools_enabled():
        args.extend([
            "-c", "apps._default.enabled=false",
            "-c", "apps.imagegen.enabled=true",
            "-c", "apps.image_gen.enabled=true",
            "-c", "features.multi_agent=false",
            "-c", "features.shell_tool=false",
            "-c", "features.unified_exec=false",
            "-c", 'default_permissions=":read-only"',
        ])
    return args


def _claude_mcp_args(mcp: dict[str, Any]) -> list[str]:
    mcp_servers = {
        mcp_server_name(mcp): {
            "type": "http",
            "url": str(mcp.get("url") or ""),
            "headers": {"Authorization": mcp_authorization(mcp)},
        }
    }
    args = [
        "--mcp-config",
        json.dumps({"mcpServers": mcp_servers}, separators=(",", ":")),
        "--strict-mcp-config",
    ]
    allowed_tools = [
        f"mcp__{mcp_server_name(mcp)}__{name}"
        for name in mcp_tool_names(mcp)
    ]
    allowed_tool_names = [str(item) for item in allowed_tools if str(item)]
    if allowed_tool_names:
        args.extend(["--allowedTools", ",".join(allowed_tool_names)])
    if not provider_native_tools_enabled():
        args.extend(["--disallowedTools", ",".join(CLAUDE_CODE_NATIVE_TOOLS)])
    return args


def _mcp_auth_env(mcp: dict[str, Any]) -> dict[str, str]:
    token = str(mcp.get("token") or "")
    authorization = mcp_authorization(mcp)
    env: dict[str, str] = {}
    if token:
        env[LEMMA_MCP_TOKEN_ENV] = token
    if authorization:
        env[LEMMA_MCP_AUTHORIZATION_ENV] = authorization
    return env


def _configured_provider_cwd(harness_kind: str) -> Path | None:
    specific_env = os.getenv(f"LEMMA_DAEMON_{harness_kind}_CWD")
    generic_env = os.getenv("LEMMA_DAEMON_CWD")
    configured = specific_env or generic_env
    if not configured:
        return None
    return Path(configured).expanduser().resolve()


def _default_provider_cwd(harness_kind: str) -> Path:
    return (Path.home() / DEFAULT_CWD_DIRS.get(harness_kind, "lemma-daemon")).resolve()


def _conversation_provider_cwd(harness_kind: str, mcp: dict[str, Any]) -> Path:
    conversation_id = mcp_conversation_id(mcp)
    root = provider_cwd(harness_kind)
    if conversation_id and conversation_id != "default":
        return root / "conversations" / conversation_id
    return root


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, list | tuple):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, dict):
        return (
            "{"
            + ", ".join(f"{key} = {_toml_value(item)}" for key, item in value.items())
            + "}"
        )
    raise TypeError(f"Unsupported TOML override value: {type(value).__name__}")


def _opencode_data_home() -> str:
    configured = os.getenv("LEMMA_DAEMON_OPENCODE_DATA_HOME")
    if configured:
        return configured
    return str(Path(tempfile.gettempdir()) / "lemma-opencode-daemon-data")


def _is_provider_scoped_lemma_tool_name(value: str) -> bool:
    prefixes = (
        f"mcp.{LEMMA_MCP_SERVER_NAME}.",
        f"mcp__{LEMMA_MCP_SERVER_NAME}__",
        f"{LEMMA_MCP_SERVER_NAME}_",
        f"{LEMMA_MCP_SERVER_NAME}.",
        "mcp.lemma-tools.",
        "mcp__lemma-tools__",
        "lemma-tools_",
        "lemma-tools.",
    )
    return value.startswith(prefixes)


def _rewrite_upstream_mcp_urls(value: Any, *, base_url: str) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _rewrite_upstream_mcp_urls(item, base_url=base_url)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_rewrite_upstream_mcp_urls(item, base_url=base_url) for item in value]
    if isinstance(value, str):
        return _rewrite_upstream_mcp_url(value, base_url=base_url)
    return value


def _rewrite_upstream_mcp_url(value: str, *, base_url: str) -> str:
    # Recognize both the conversation MCP (/agent-runtime/conversations/{id}/mcp)
    # and the pod MCP (/agent-runtime/pods/{id}/mcp).
    if "/agent-runtime/" not in value or "/mcp" not in value:
        return value
    embedded_pattern = re.compile(
        r"https?://[^\s\"'}]+/agent-runtime/(?:conversations|pods)/[^\s\"'}]+/mcp"
    )
    if embedded_pattern.search(value):
        return embedded_pattern.sub(
            lambda match: _rewrite_single_upstream_mcp_url(match.group(0), base_url=base_url),
            value,
        )
    return _rewrite_single_upstream_mcp_url(value, base_url=base_url)


def _rewrite_single_upstream_mcp_url(value: str, *, base_url: str) -> str:
    try:
        source = urlsplit(value)
        target = urlsplit(base_url.rstrip("/"))
    except ValueError:
        return value
    if not source.scheme or not source.netloc or not target.scheme or not target.netloc:
        return value
    return urlunsplit((target.scheme, target.netloc, source.path, source.query, source.fragment))


def _deep_merge(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        else:
            target[key] = value
