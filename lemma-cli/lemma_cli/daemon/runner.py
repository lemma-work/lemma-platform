from __future__ import annotations

import asyncio
import contextlib
import json
import random
from typing import Any, Callable

from ._logging import log as daemon_log, preview as _preview, redact as _redact
from ._utils import bounded_error_detail
from .catalog import discover_harness_catalog
from .config import ensure_config, save_config, daemon_ws_url, device_info
from .harnesses.registry import get_harness
from .mcp import (
    payload_with_reachable_mcp_urls,
    provider_command,
    provider_cwd_for_run,
    provider_environment,
)

# Reconnect backoff: a dropped backend connection must not kill the daemon — it
# should wait and reconnect so it keeps serving runs. Full-jitter exponential
# backoff, capped, to avoid hammering the server on a sustained outage.
_RECONNECT_BASE_DELAY_SECONDS = 1.0
_RECONNECT_MAX_DELAY_SECONDS = 30.0


def reconnect_delay_seconds(attempt: int) -> float:
    """Full-jitter exponential backoff delay (seconds) for a reconnect ``attempt``."""
    ceiling = min(
        _RECONNECT_MAX_DELAY_SECONDS,
        _RECONNECT_BASE_DELAY_SECONDS * (2 ** max(0, attempt)),
    )
    return random.uniform(0.0, ceiling)


def ssl_for_ws_url(ws_url: str, *, verify_ssl: bool) -> Any:
    """Build the ``ssl`` argument for ``websockets.connect``.

    The websockets client rejects ``ssl=None`` for a ``wss://`` URI (it can't tell
    "use the default TLS context" from "no TLS"), so a secure URL must get an
    explicit value: ``True`` for a verified handshake, or an unverified
    ``SSLContext`` when verification is disabled. Passing ``ssl=False`` is wrong for
    ``wss://`` — asyncio treats it as plaintext and would dial a TLS port without
    TLS. For a plain ``ws://`` URL there is no TLS, so ``None`` is the only value
    websockets accepts.
    """
    if ws_url.startswith("ws://"):
        return None
    if verify_ssl:
        return True
    import ssl as ssl_module  # noqa: PLC0415

    context = ssl_module.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl_module.CERT_NONE
    return context


async def run_daemon(
    *,
    base_url: str,
    token: str,
    verify_ssl: bool,
    debug: bool = False,
    token_provider: Callable[[], str] | None = None,
    connect_factory: Callable[[str], Any] | None = None,
    max_reconnect_attempts: int | None = None,
) -> None:
    """Run the daemon, reconnecting with backoff if the connection drops.

    ``token_provider`` (optional) is called before each (re)connect to obtain a
    fresh token. ``connect_factory`` / ``max_reconnect_attempts`` exist for tests;
    in production a real websocket connector is used and reconnects are unbounded.
    """
    from ._logging import set_debug
    set_debug(debug)
    try:
        import websockets
        from websockets.exceptions import InvalidStatus, WebSocketException
    except ImportError as exc:
        import click
        raise click.ClickException(
            "Missing dependency 'websockets'. Reinstall the CLI to enable daemon mode: "
            "pip install --upgrade lemma-terminal (or: pip install websockets)."
        ) from exc

    config = ensure_config()
    ws_url = daemon_ws_url(base_url)
    catalog = discover_harness_catalog()
    ssl_option = ssl_for_ws_url(ws_url, verify_ssl=verify_ssl)

    if connect_factory is None:
        def connect_factory(current_token: str) -> Any:
            return websockets.connect(
                ws_url,
                additional_headers={"Authorization": f"Bearer {current_token}"},
                ssl=ssl_option,
            )

    # `backoff_attempt` grows the delay while we can't stay connected and resets
    # once a connection is established; `reconnects` is a monotonic count of
    # reconnect cycles used only to bound the loop in tests.
    backoff_attempt = 0
    reconnects = 0
    while True:
        current_token = token_provider() if token_provider is not None else token
        daemon_log("connecting websocket", {"url": ws_url, "attempt": backoff_attempt})
        try:
            async with connect_factory(current_token) as websocket:
                backoff_attempt = 0  # reset backoff once we're connected
                await _serve_connection(
                    websocket,
                    config=config,
                    catalog=catalog,
                    base_url=base_url,
                )
            # _serve_connection returned: the server closed the socket cleanly.
            daemon_log("websocket closed; reconnecting", {})
        except asyncio.CancelledError:
            raise
        except InvalidStatus as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            if status_code in {401, 403}:
                import click
                raise click.ClickException(
                    "Daemon websocket authentication failed. Run `lemma auth login` and try again."
                ) from exc
            daemon_log("websocket rejected; will retry", {"status": status_code})
        except (OSError, WebSocketException) as exc:
            daemon_log("websocket connection error; will retry", {"error": str(exc)})

        reconnects += 1
        if max_reconnect_attempts is not None and reconnects > max_reconnect_attempts:
            daemon_log("giving up reconnect", {"reconnects": reconnects})
            return
        delay = reconnect_delay_seconds(backoff_attempt)
        backoff_attempt += 1
        daemon_log(
            "reconnecting after backoff",
            {"delay_seconds": round(delay, 2), "reconnects": reconnects},
        )
        await asyncio.sleep(delay)


async def _serve_connection(
    websocket: Any,
    *,
    config: dict[str, Any],
    catalog: Any,
    base_url: str,
) -> None:
    """Run the ready handshake + message loop until the connection closes.

    In-flight run tasks are cancelled when the connection drops so they don't leak
    across reconnects.
    """
    await _send_json(
        websocket,
        {
            "type": "daemon.ready",
            "payload": {
                "device_key": config["device_key"],
                "display_name": config.get("display_name")
                or __import__("socket").gethostname(),
                "device_info": device_info(),
                "harness_catalog": catalog,
            },
        },
    )
    daemon_log("connected; waiting for runs", {"catalog": catalog})
    active_runs: dict[str, asyncio.Task[None]] = {}
    try:
        async for raw_message in websocket:
            message = json.loads(raw_message)
            message_type = message.get("type")
            daemon_log("incoming websocket message", message)
            if message_type == "daemon.ready_ack":
                config["daemon_id"] = message.get("daemon_id")
                save_config(config)
                daemon_log("ready ack", {"daemon_id": config["daemon_id"]})
                continue
            if message_type == "catalog.refresh":
                catalog = discover_harness_catalog()
                await _send_json(
                    websocket, {"type": "daemon.catalog", "payload": catalog}
                )
                daemon_log("catalog refreshed", catalog)
                continue
            if message_type == "run.start":
                agent_run_id = str(message.get("agent_run_id") or "")
                task = asyncio.create_task(
                    handle_run_start(websocket, message, base_url=base_url)
                )
                active_runs[agent_run_id] = task
                task.add_done_callback(
                    lambda _task, run_id=agent_run_id: active_runs.pop(run_id, None)
                )
                continue
            if message_type == "run.stop":
                agent_run_id = str(message.get("agent_run_id") or "")
                await _stop_active_run(
                    websocket=websocket,
                    active_runs=active_runs,
                    agent_run_id=agent_run_id,
                )
                continue
    finally:
        for task in list(active_runs.values()):
            if not task.done():
                task.cancel()


async def _stop_active_run(
    *,
    websocket: Any,
    active_runs: dict[str, asyncio.Task[None]],
    agent_run_id: str,
) -> None:
    task = active_runs.get(agent_run_id)
    if task is not None:
        task.cancel()
        return
    if agent_run_id:
        await send_run_event(websocket, agent_run_id, "stopped", {})


async def handle_run_start(
    websocket: Any,
    message: dict[str, Any],
    *,
    base_url: str | None = None,
) -> None:
    agent_run_id = str(message.get("agent_run_id") or "")
    payload = message.get("payload") if isinstance(message.get("payload"), dict) else {}
    if base_url:
        payload = payload_with_reachable_mcp_urls(payload, base_url=base_url)
    harness_kind = str(payload.get("harness_kind") or "")
    daemon_log(
        "run requested",
        {
            "agent_run_id": agent_run_id,
            "harness_kind": harness_kind,
            "model_name": payload.get("model_name"),
            "mcp": payload.get("mcp"),
            "prompt_preview": _preview(_prompt_text_preview(payload)),
        },
    )
    await send_run_event(
        websocket,
        agent_run_id,
        "status",
        {"status": "daemon provider process starting", "harness_kind": harness_kind},
    )
    try:
        result = await run_provider_command(
            payload,
            event_sink=lambda event_type, data: send_run_event(websocket, agent_run_id, event_type, data),
        )
    except asyncio.CancelledError:
        daemon_log("run cancelled", {"agent_run_id": agent_run_id})
        await send_run_event(websocket, agent_run_id, "stopped", {})
        raise
    except Exception as exc:
        error_detail = _exception_detail(exc)
        daemon_log("run failed", {"agent_run_id": agent_run_id, "error": error_detail})
        await send_run_event(websocket, agent_run_id, "error", error_detail)
        return
    daemon_log(
        "provider result",
        {
            "agent_run_id": agent_run_id,
            "returncode": result.get("returncode"),
            "stdout_preview": _preview(result.get("stdout", "")),
            "stderr_preview": _preview(result.get("stderr", "")),
            "command": result.get("command"),
        },
    )
    result["stdout"] = _strip_prompt_echo_from_stdout(payload, str(result.get("stdout") or ""))
    if result["stdout"]:
        redacted_command = _redact(result["command"])
        if not result.get("streamed_tokens"):
            await send_run_event(websocket, agent_run_id, "token", result["stdout"])
        if not result.get("streamed_messages"):
            await send_run_event(
                websocket,
                agent_run_id,
                "message",
                {
                    "role": "assistant",
                    "kind": "text",
                    "text": result["stdout"],
                    "metadata": {
                        "user_daemon": True,
                        "harness_kind": harness_kind,
                        "command": redacted_command,
                    },
                },
            )
    if result["returncode"] == 0:
        await send_run_event(
            websocket,
            agent_run_id,
            "completed",
            {"returncode": result["returncode"], "stderr": result["stderr"]},
        )
        return
    await send_run_event(
        websocket,
        agent_run_id,
        "error",
        result["stderr"] or f"Provider command failed with exit code {result['returncode']}",
    )


async def run_provider_command(
    payload: dict[str, Any],
    *,
    event_sink: Any = None,
) -> dict[str, Any]:
    harness_kind = str(payload.get("harness_kind") or "")
    prompt = payload.get("prompt") if isinstance(payload.get("prompt"), dict) else {}
    model_name = str(payload.get("model_name") or "default")
    mcp = payload.get("mcp") if isinstance(payload.get("mcp"), dict) else {}

    if harness_kind in {"CODEX", "CLAUDE_CODE", "OPENCODE", "CURSOR", "ANTIGRAVITY"}:
        harness = get_harness(harness_kind)
        allow_recovery = harness_kind == "CODEX"
        system_prompt, user_prompt, session_id = _prompt_parts(prompt, allow_recovery_system_prompt=allow_recovery)
        return await harness.run(
            model_name=model_name,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            session_id=session_id,
            mcp=mcp,
            event_sink=event_sink,
            stop_event=None,
        )

    # Generic one-shot provider via command template
    system_prompt, user_prompt, session_id = _prompt_parts(prompt)
    prompt_text = _provider_prompt_text(system_prompt=system_prompt, user_prompt=user_prompt)
    command = provider_command(
        harness_kind=harness_kind,
        model_name=model_name,
        prompt_text=prompt_text,
        mcp=mcp,
    )
    if not command:
        raise RuntimeError(f"No provider command configured for {harness_kind}")
    from .mcp import provider_command_template
    stdin_text = None if "{prompt}" in provider_command_template(harness_kind) else prompt_text
    cwd = provider_cwd_for_run(harness_kind, mcp)
    env = provider_environment(harness_kind=harness_kind, mcp=mcp)
    daemon_log("start one-shot provider", {"harness_kind": harness_kind, "command": command, "cwd": str(cwd)})
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE if stdin_text is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd),
        env=env,
    )
    try:
        stdout_bytes, stderr_bytes = await process.communicate(
            stdin_text.encode() if stdin_text is not None else None
        )
    except asyncio.CancelledError:
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
        raise
    return {
        "command": command,
        "cwd": str(cwd),
        "returncode": int(process.returncode or 0),
        "stdout": stdout_bytes.decode(errors="replace").strip(),
        "stderr": stderr_bytes.decode(errors="replace").strip(),
    }


async def send_run_event(
    websocket: Any,
    agent_run_id: str,
    event_type: str,
    data: Any,
) -> None:
    daemon_log("send run event", {"agent_run_id": agent_run_id, "event_type": event_type, "data": data})
    await _send_json(
        websocket,
        {
            "type": "run.event",
            "agent_run_id": agent_run_id,
            "event": {"type": event_type, "data": data},
        },
    )


async def _send_json(websocket: Any, payload: dict[str, Any]) -> None:
    """Send a JSON message, tolerating a dropped socket.

    A failed send (e.g. the connection dropped mid-run) must not crash the daemon —
    the reconnect loop re-establishes the connection; the dropped event is logged.
    """
    try:
        await websocket.send(json.dumps(payload))
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - transport guard
        daemon_log(
            "websocket send failed; dropping message",
            {"type": payload.get("type"), "error": str(exc)},
        )


def _prompt_parts(
    prompt: dict[str, Any],
    *,
    allow_recovery_system_prompt: bool = False,
) -> tuple[str, str, str | None]:
    system_prompt = str(prompt.get("system_prompt") or "")
    if not system_prompt and allow_recovery_system_prompt:
        system_prompt = str(prompt.get("recovery_system_prompt") or "")
    user_prompt = str(prompt.get("user_prompt") or "")
    session_id = str(prompt.get("session_id") or "").strip() or None
    if not user_prompt:
        raise RuntimeError("Daemon prompt payload is missing user_prompt")
    if session_id is None and not system_prompt:
        raise RuntimeError("Daemon prompt payload is missing system_prompt for new session")
    return system_prompt, user_prompt, session_id


def _provider_prompt_text(*, system_prompt: str, user_prompt: str) -> str:
    if not system_prompt:
        return user_prompt
    return "\n\n".join(
        part
        for part in (system_prompt, _conversation_section(user_prompt))
        if part.strip()
    )


def _conversation_section(user_prompt: str) -> str:
    if not user_prompt:
        return ""
    if user_prompt.lstrip().startswith("#"):
        return user_prompt
    return "# Conversation\n" + user_prompt


def _prompt_text_preview(payload: dict[str, Any]) -> str:
    prompt = payload.get("prompt") if isinstance(payload.get("prompt"), dict) else {}
    system_prompt = str(prompt.get("system_prompt") or "")
    user_prompt = str(prompt.get("user_prompt") or "")
    if not system_prompt and not user_prompt:
        return ""
    return _provider_prompt_text(system_prompt=system_prompt, user_prompt=user_prompt)


def _strip_prompt_echo_from_stdout(payload: dict[str, Any], stdout: str) -> str:
    if not stdout:
        return ""
    prompt = payload.get("prompt") if isinstance(payload.get("prompt"), dict) else {}
    system_prompt = str(prompt.get("system_prompt") or "")
    user_prompt = str(prompt.get("user_prompt") or "")
    candidates = [
        _provider_prompt_text(system_prompt=system_prompt, user_prompt=user_prompt),
        user_prompt,
    ]
    stripped_text = stdout.strip()
    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate:
            continue
        if stripped_text == candidate:
            return ""
        if stripped_text.startswith(candidate):
            return stripped_text[len(candidate):].lstrip()
    return stdout


def _exception_detail(exc: Exception) -> str:
    from .harnesses.codex import JsonRpcRequestError
    if isinstance(exc, JsonRpcRequestError):
        return str(exc)
    return bounded_error_detail(f"{type(exc).__name__}: {exc}")
