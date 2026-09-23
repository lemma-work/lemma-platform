"""One request to the Desktop guest, over the native bridge.

Extracted from the provider, which was at the size limit. It is the whole of
the transport: framing a request, running the bridge off the event loop,
bounding its size in both directions, and turning an error envelope into the
two exception types the rest of the provider branches on.

Nothing here knows what a sandbox is, which is why it moved cleanly.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from datetime import datetime, timezone
from typing import Any

from app.core.concurrency.offload import run_blocking
from app.modules.workspace.providers.lemma_local_config import (
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    LocalBridgeError,
    LocalBridgeNotFound,
)

#: What the guest answers with: the `result` object of a bridge envelope. Named
#: here so the provider can speak about it without spelling `Any` again --- the
#: shape is the guest's to define, and every caller narrows it immediately.
BridgeResult = dict[str, Any]


async def call_bridge(
    executable: str,
    request_timeout_seconds: float,
    operation: str,
    parameters: dict[str, object],
    *,
    deadline_at: datetime,
) -> BridgeResult:
    encoded = json.dumps(
        {"version": 1, "operation": operation, "parameters": parameters},
        separators=(",", ":"),
    )
    if len(encoded.encode()) > MAX_REQUEST_BYTES:
        raise LocalBridgeError("managed runtime request exceeds 1 MiB", retryable=False)
    remaining = (deadline_at - datetime.now(timezone.utc)).total_seconds()
    if remaining <= 0:
        raise asyncio.TimeoutError
    timeout = min(remaining, request_timeout_seconds)

    def invoke() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [executable, "request"],
            input=f"{encoded}\n",
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    try:
        # The bridge is a blocking subprocess, so it is run off the event
        # loop; leaving it inline would stall every other request. Its own
        # limiter rather than ``external_http``: this call is bounded by the
        # request deadline, not an HTTP timeout, so a burst of long sandbox
        # operations would otherwise hold every slot the connector SDKs use.
        process = await run_blocking(invoke, limiter="local_bridge")
    except subprocess.TimeoutExpired as exc:
        raise asyncio.TimeoutError from exc
    except OSError as exc:
        # The bridge could not be started at all: missing, or not executable.
        # Definitive, and in this module's vocabulary so callers report a
        # sandbox that cannot be reached rather than an unhandled 500.
        #
        # `LocalBridgeError` and not `LocalBridgeNotFound`, which here means
        # "the guest says that sandbox does not exist" -- an outcome `_mutate`
        # treats as already achieved, so a bridge nobody can run would report a
        # release, a delete or a purge as done.
        raise LocalBridgeError(
            f"managed runtime bridge could not be started: {exc.strerror or exc}",
            code="local_runtime_unavailable",
            retryable=False,
        ) from exc

    return _result_of(process)


def _result_of(process: "subprocess.CompletedProcess[str]") -> BridgeResult:
    """The `result` object, or the failure the guest described instead.

    Separate from the call above because the two halves fail for unrelated
    reasons: one is about whether the bridge ran, and this one is about whether
    what it said can be believed.
    """
    if len(process.stdout.encode()) > MAX_RESPONSE_BYTES:
        raise LocalBridgeError("managed runtime response exceeds 4 MiB")
    try:
        response = json.loads(process.stdout)
    except json.JSONDecodeError as exc:
        diagnostic = process.stderr.splitlines()[:1]
        suffix = f": {diagnostic[0]}" if diagnostic else ""
        raise LocalBridgeError(
            f"managed runtime response was not JSON{suffix}"
        ) from exc
    if not isinstance(response, dict):
        raise LocalBridgeError("managed runtime response was not an object")

    if process.returncode != 0 or response.get("ok") is not True:
        raise _described_failure(response)

    result = response.get("result")
    if not isinstance(result, dict):
        raise LocalBridgeError("managed runtime response omitted its result")
    return result


def _described_failure(response: dict[str, Any]) -> LocalBridgeError:
    """The guest's own error envelope, in this module's vocabulary."""
    error = response.get("error")
    details = error if isinstance(error, dict) else {}
    code = str(details.get("code") or "local_runtime_failed")
    failure = LocalBridgeNotFound if code == "not_found" else LocalBridgeError
    return failure(
        str(details.get("message") or "managed runtime request failed"),
        code=code,
        retryable=bool(details.get("retryable", True)),
    )
