from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .config import (
    DEFAULT_CONFIG_PATH,
    ENV_SERVER_NAME,
    build_env_server_config,
    get_access_token_from_config,
    get_server_config,
    load_config,
    normalize_server_config,
    resolve_base_url,
    resolve_verify_ssl,
    should_use_env_server,
)
from .errors import LemmaConfigError


@dataclass(frozen=True)
class LemmaSettings:
    base_url: str
    token: str
    org_id: str | None = None
    pod_id: str | None = None
    timeout: float = 30.0
    verify_ssl: bool = True
    server: str | None = None
    config_path: Path = DEFAULT_CONFIG_PATH


def load_settings(
    *,
    base_url: str | None = None,
    token: str | None = None,
    org_id: str | None = None,
    pod_id: str | None = None,
    timeout: float = 30.0,
    verify_ssl: bool | None = None,
    server: str | None = None,
    config_path: Path | None = None,
) -> LemmaSettings:
    """Resolve where to dial and what to send, the way the CLI does.

    The server is chosen first, and the endpoint and the credential then both
    come from it, so the two can never be crossed:

    * ``server=`` (or ``LEMMA_SERVER``) names a server in ``~/.lemma/config.json``;
    * with no server named, ``LEMMA_TOKEN`` selects the synthetic ``env`` server,
      whose endpoints are ``LEMMA_BASE_URL`` and ``LEMMA_AUTH_URL`` and otherwise
      the public defaults;
    * with neither, the config file's active server is used.

    ``base_url=`` and ``token=`` always win over whichever server was chosen.
    ``LEMMA_BASE_URL`` is read only for the ``env`` server -- a named server
    carries its own endpoint, and letting the environment redirect it is how a
    token reaches a host nobody asked for.
    """
    path = config_path or DEFAULT_CONFIG_PATH
    requested_server = server or os.getenv("LEMMA_SERVER")

    # The same rule the CLI applies in `build_state`: a token that came from the
    # environment is paired with environment endpoints, never with whatever
    # server ~/.lemma/config.json happens to have selected. Resolving the two
    # independently is how a cloud token ends up dialed at a self-hosted or
    # scratch server the caller never named.
    use_env_server = should_use_env_server(requested_server)
    if use_env_server:
        selected_server = ENV_SERVER_NAME
        config = build_env_server_config()
    else:
        root, selected_server = normalize_server_config(
            load_config(path),
            selected_server=requested_server,
        )
        config = get_server_config(root, selected_server)
    defaults = (
        config.get("defaults") if isinstance(config.get("defaults"), dict) else {}
    )

    try:
        resolved_base_url = resolve_base_url(base_url, config, use_env=use_env_server)
    except ValueError as exc:
        # A selected server with no reachable endpoint (Desktop's local server
        # while the runtime is down). Say so instead of quietly dialing the
        # public default with a credential minted for somewhere else.
        raise LemmaConfigError(str(exc)) from exc
    resolved_token = token or get_access_token_from_config(config)
    if not resolved_token:
        raise LemmaConfigError(
            "Missing Lemma token. Pass token=..., set LEMMA_TOKEN, or run `lemma auth login`."
        )

    return LemmaSettings(
        base_url=str(resolved_base_url).rstrip("/"),
        token=resolved_token,
        org_id=org_id or os.getenv("LEMMA_ORG_ID") or defaults.get("org_id"),
        pod_id=pod_id or os.getenv("LEMMA_POD_ID") or defaults.get("pod_id"),
        timeout=timeout,
        verify_ssl=verify_ssl if verify_ssl is not None else resolve_verify_ssl(),
        server=selected_server,
        config_path=path,
    )
