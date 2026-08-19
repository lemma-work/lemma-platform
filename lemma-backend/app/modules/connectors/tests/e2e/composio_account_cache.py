"""Reuse one real Composio connection across every test that needs one.

Consenting to OAuth in a browser is the one part of a real Composio test a
machine cannot do. Doing it per test would make the suite unusable, so the
connected-account id is cached on disk and reused: you consent once, and every
later scenario -- and every later run -- picks up the same live account.

The cache lives outside the repository (``~/.lemma/e2e/composio-accounts.json``,
mode 0600) because it holds account identifiers tied to a real inbox. It is
validated against Composio before use, so a revoked or expired connection
re-prompts rather than failing the suite with a confusing error.

Precedence, highest first:

1. ``LEMMA_E2E_COMPOSIO_ACCOUNT_<TOOLKIT>`` -- an id supplied directly, for a
   shared machine or a one-off.
2. The on-disk cache, if Composio still reports the connection ACTIVE.
3. A fresh browser consent, but only when ``RUN_HUMAN_OAUTH=1``. Otherwise the
   test skips: a suite must never silently block waiting for a human.
"""

from __future__ import annotations

import json
import os
import time
import webbrowser
from pathlib import Path
from typing import Any

import pytest

_CACHE_PATH = Path.home() / ".lemma" / "e2e" / "composio-accounts.json"
_TERMINAL_STATES = {"FAILED", "EXPIRED", "REVOKED", "DELETED"}
_POLL_INTERVAL_SECONDS = 3.0


def human_oauth_enabled() -> bool:
    return os.getenv("RUN_HUMAN_OAUTH") == "1"


def _read_cache() -> dict[str, Any]:
    try:
        return json.loads(_CACHE_PATH.read_text())
    except OSError, ValueError:
        # A missing or corrupt cache is not an error: it just means we have not
        # connected yet, or the file was hand-edited.
        return {}


def _write_cache(data: dict[str, Any]) -> None:
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_PATH.write_text(json.dumps(data, indent=2, sort_keys=True))
    _CACHE_PATH.chmod(0o600)


def _connection_status(composio: Any, account_id: str) -> str | None:
    try:
        account = composio.connected_accounts.get(account_id)
    except Exception:
        # Composio 404s a deleted connection; treat any lookup failure as
        # "unusable" so we fall through to reconnecting.
        return None
    return str(getattr(account, "status", "") or "").upper() or None


def _wait_until_active(composio: Any, account_id: str, *, timeout: float) -> None:
    # Plain time.sleep, not waiters.eventually(): the Composio SDK is sync, and
    # every function in this module is plain `def`, called from sync test setup
    # rather than from inside a test's event loop.
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = _connection_status(composio, account_id)
        if last == "ACTIVE":
            return
        if last in _TERMINAL_STATES:
            pytest.fail(f"Composio connection {account_id} ended in state {last}")
        time.sleep(_POLL_INTERVAL_SECONDS)
    pytest.fail(
        f"Timed out after {timeout:.0f}s waiting for {account_id} to become "
        f"ACTIVE (last status: {last})"
    )


def _connect_interactively(
    composio: Any, *, toolkit: str, user_id: str, timeout: float
) -> str:
    """Open consent in a browser and block until the connection goes ACTIVE."""
    auth_config = composio.auth_configs.create(
        toolkit=toolkit, options={"type": "use_composio_managed_auth"}
    )
    request = composio.connected_accounts.initiate(
        user_id=user_id, auth_config_id=auth_config.id
    )
    url = getattr(request, "redirect_url", None)
    if not url:
        pytest.fail(f"Composio returned no consent URL for {toolkit}")

    # Printed as well as opened: on a headless or remote machine the browser
    # call is a no-op and the operator needs the URL itself.
    print(f"\n=== Composio consent required for '{toolkit}' ===\n{url}\n")
    webbrowser.open(url)

    _wait_until_active(composio, request.id, timeout=timeout)
    return request.id


def resolve_connected_account(
    composio: Any,
    *,
    toolkit: str,
    user_id: str,
    consent_timeout: float = 300.0,
) -> str:
    """Return a live Composio connected-account id for ``toolkit``.

    Skips (rather than fails) when no account exists and nobody is available to
    consent, so the suite stays runnable unattended.
    """
    override = os.getenv(f"LEMMA_E2E_COMPOSIO_ACCOUNT_{toolkit.upper()}")
    if override:
        return override

    cache = _read_cache()
    cached = (cache.get(toolkit) or {}).get("connected_account_id")
    if cached and _connection_status(composio, cached) == "ACTIVE":
        return cached

    if not human_oauth_enabled():
        pytest.skip(
            f"No cached Composio account for '{toolkit}'. Run once with "
            f"RUN_HUMAN_OAUTH=1 to consent in a browser; the connection is then "
            f"cached at {_CACHE_PATH} and reused by every later run."
        )

    account_id = _connect_interactively(
        composio, toolkit=toolkit, user_id=user_id, timeout=consent_timeout
    )
    cache[toolkit] = {"connected_account_id": account_id, "user_id": user_id}
    _write_cache(cache)
    return account_id


def forget_connected_account(toolkit: str) -> None:
    """Drop a cached entry, so the next run reconnects."""
    cache = _read_cache()
    if cache.pop(toolkit, None) is not None:
        _write_cache(cache)
