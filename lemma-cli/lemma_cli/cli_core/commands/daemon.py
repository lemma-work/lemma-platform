"""
Compatibility shim — the daemon implementation lives in lemma_cli.daemon.
This file re-exports all symbols that tests and other code access here.
"""
from __future__ import annotations

import sys
import types

# ── CLI app ──────────────────────────────────────────────────────────────────
from lemma_cli.daemon.commands import (
    app,
    discover_daemon,
    logs_daemon,
    start_background,
    start_daemon,
    status_daemon,
    stop_daemon,
)

# ── Runner / dispatch ─────────────────────────────────────────────────────────
from lemma_cli.daemon.runner import (
    handle_run_start,
    run_daemon as run_foreground_daemon,
    run_provider_command,
    send_run_event,
    _prompt_parts,
    _provider_prompt_text,
    _prompt_text_preview,
    _strip_prompt_echo_from_stdout,
    _stop_active_run,
    _RunEventSink,
    _HeldRun,
)

# ── Logging helpers ───────────────────────────────────────────────────────────
from lemma_cli.daemon._logging import (
    log as _daemon_log,
    set_debug as _set_daemon_debug,
    redact as _redact,
)

# ── Config / device ───────────────────────────────────────────────────────────
from lemma_cli.daemon.config import (
    DAEMON_DIR,
    DAEMON_CONFIG_PATH,
    DAEMON_PID_PATH,
    DAEMON_LOG_PATH,
    ensure_config as ensure_daemon_config,
    load_config as load_daemon_config,
    save_config as save_daemon_config,
    device_info,
    daemon_ws_url,
    read_pid,
    process_is_running,
    read_daemon_status,
    write_daemon_status,
    clear_daemon_status,
)

# ── Catalog ───────────────────────────────────────────────────────────────────
from lemma_cli.daemon.catalog import (
    HARNESS_BINARIES,
    CLAUDE_CODE_MODEL_CATALOG,
    CLAUDE_CODE_STANDARD_MODEL_BY_ALIAS,
    discover_harness_catalog,
    discover_harness,
    discover_harness_models,
    discover_harness_model_entries,
    normalize_provider_model_name,
    configured_harness_models,
    binary_version,
    run_catalog_command,
)

# ── MCP / provider command helpers ────────────────────────────────────────────
from lemma_cli.daemon.mcp import (
    DEFAULT_COMMAND_TEMPLATES,
    LEMMA_MCP_SERVER_NAME,
    LEMMA_MCP_TOKEN_ENV,
    LEMMA_MCP_AUTHORIZATION_ENV,
    provider_command,
    provider_command_template,
    provider_cwd,
    provider_cwd_for_run,
    provider_environment,
    payload_with_reachable_mcp_urls as _payload_with_reachable_mcp_urls,
)

# ── Process helpers ───────────────────────────────────────────────────────────
from lemma_cli.daemon.process import (
    STREAM_READER_LIMIT,
    drain_stream as _read_process_stream,
    terminate_gracefully as _terminate_process,
)

# ── Harness base ──────────────────────────────────────────────────────────────
from lemma_cli.daemon.harnesses.base import StreamTextState as _StreamTextState

# ── Codex harness (most test-heavy) ──────────────────────────────────────────
from lemma_cli.daemon.harnesses.codex import (
    CODEX_WORKER_TTL_SECONDS_ENV,
    DAEMON_TURN_TIMEOUT_SECONDS_ENV,
    JsonRpcRequestError as _JsonRpcRequestError,
    JsonRpcProcess as _JsonRpcProcess,
    CodexWorkerPool as _CodexAppServerPool,
    _CODEX_APP_SERVER_POOL,
    codex_text_delta as _codex_text_delta,
    codex_completed_assistant_text as _codex_completed_assistant_text,
    codex_new_completed_assistant_text as _codex_new_completed_assistant_text,
    codex_tool_call_event as _codex_tool_call_event,
    codex_tool_return_event as _codex_tool_return_event,
    codex_worker_ttl_seconds as _codex_worker_ttl_seconds,
    daemon_turn_timeout_seconds as _daemon_turn_timeout_seconds,
)

# ── OpenCode harness ──────────────────────────────────────────────────────────
from lemma_cli.daemon.harnesses.opencode import (
    _run_opencode_turn,
    _opencode_request,
    _accept_lemma_opencode_permissions,
)

# ── Console (used by tests to monkeypatch output) ─────────────────────────────
from lemma_cli.cli_core.state import console

__all__ = [
    # app
    "app",
    "discover_daemon",
    "logs_daemon",
    "start_background",
    "start_daemon",
    "status_daemon",
    "stop_daemon",
    # runner
    "handle_run_start",
    "run_foreground_daemon",
    "run_provider_command",
    "send_run_event",
    "_prompt_parts",
    "_provider_prompt_text",
    "_prompt_text_preview",
    "_strip_prompt_echo_from_stdout",
    "_stop_active_run",
    "_RunEventSink",
    "_HeldRun",
    # logging
    "_daemon_log",
    "_set_daemon_debug",
    "_redact",
    # config
    "DAEMON_DIR",
    "DAEMON_CONFIG_PATH",
    "DAEMON_PID_PATH",
    "DAEMON_LOG_PATH",
    "ensure_daemon_config",
    "load_daemon_config",
    "save_daemon_config",
    "device_info",
    "daemon_ws_url",
    "read_pid",
    "process_is_running",
    "read_daemon_status",
    "write_daemon_status",
    "clear_daemon_status",
    # catalog
    "HARNESS_BINARIES",
    "CLAUDE_CODE_MODEL_CATALOG",
    "CLAUDE_CODE_STANDARD_MODEL_BY_ALIAS",
    "discover_harness_catalog",
    "discover_harness",
    "discover_harness_models",
    "discover_harness_model_entries",
    "normalize_provider_model_name",
    "configured_harness_models",
    "binary_version",
    "run_catalog_command",
    # MCP
    "DEFAULT_COMMAND_TEMPLATES",
    "LEMMA_MCP_SERVER_NAME",
    "LEMMA_MCP_TOKEN_ENV",
    "LEMMA_MCP_AUTHORIZATION_ENV",
    "provider_command",
    "provider_command_template",
    "provider_cwd",
    "provider_cwd_for_run",
    "provider_environment",
    "_payload_with_reachable_mcp_urls",
    # process
    "STREAM_READER_LIMIT",
    "_read_process_stream",
    "_terminate_process",
    # harness base
    "_StreamTextState",
    # codex
    "CODEX_WORKER_TTL_SECONDS_ENV",
    "DAEMON_TURN_TIMEOUT_SECONDS_ENV",
    "_JsonRpcRequestError",
    "_JsonRpcProcess",
    "_CodexAppServerPool",
    "_CODEX_APP_SERVER_POOL",
    "_codex_text_delta",
    "_codex_completed_assistant_text",
    "_codex_new_completed_assistant_text",
    "_codex_tool_call_event",
    "_codex_tool_return_event",
    "_codex_worker_ttl_seconds",
    "_daemon_turn_timeout_seconds",
    # opencode
    "_run_opencode_turn",
    "_opencode_request",
    "_accept_lemma_opencode_permissions",
    # console
    "console",
]


# ── Monkeypatch forwarding ─────────────────────────────────────────────────────
# When test code does `monkeypatch.setattr(daemon, "X", value)`, Python sets the
# attribute on this module object. For attributes whose canonical home is a
# sub-module, we override __setattr__ to also update the canonical location so
# that the running code sees the patch.

_SETATTR_FORWARD_MAP: dict[str, tuple[str, str]] = {
    "_CODEX_APP_SERVER_POOL": ("lemma_cli.daemon.harnesses.codex", "_CODEX_APP_SERVER_POOL"),
    "_JsonRpcProcess": ("lemma_cli.daemon.harnesses.codex", "JsonRpcProcess"),
    "provider_cwd": ("lemma_cli.daemon.mcp", "provider_cwd"),
    "_opencode_request": ("lemma_cli.daemon.harnesses.opencode", "_opencode_request"),
    "_accept_lemma_opencode_permissions": (
        "lemma_cli.daemon.harnesses.opencode",
        "_accept_lemma_opencode_permissions",
    ),
}


class _ShimModule(types.ModuleType):
    def __setattr__(self, name: str, value: object) -> None:
        if name in _SETATTR_FORWARD_MAP:
            import importlib  # noqa: PLC0415
            mod_name, attr_name = _SETATTR_FORWARD_MAP[name]
            mod = importlib.import_module(mod_name)
            setattr(mod, attr_name, value)
        object.__setattr__(self, name, value)


sys.modules[__name__].__class__ = _ShimModule
