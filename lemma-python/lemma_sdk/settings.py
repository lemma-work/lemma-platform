from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .config import (
    DEFAULT_BASE_URL,
    DEFAULT_CONFIG_PATH,
    get_access_token_from_config,
    get_server_config,
    load_config,
    normalize_server_config,
    resolve_verify_ssl,
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
    path = config_path or DEFAULT_CONFIG_PATH
    root, selected_server = normalize_server_config(
        load_config(path),
        selected_server=server,
    )
    config = get_server_config(root, selected_server)
    defaults = (
        config.get("defaults") if isinstance(config.get("defaults"), dict) else {}
    )

    resolved_base_url = (
        base_url
        or os.getenv("LEMMA_BASE_URL")
        or config.get("base_url")
        or DEFAULT_BASE_URL
    )
    resolved_token = (
        token or os.getenv("LEMMA_TOKEN") or get_access_token_from_config(config)
    )
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
