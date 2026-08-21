"""UI-free scope state for the TUI.

All reads and writes go through the same config helpers the CLI uses, so a
server/org/pod switch made here is immediately visible to `lemma ...` commands
and vice versa.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lemma_sdk.config import (
    get_server_config,
    normalize_server_name,
    save_config,
    server_names,
)

from ..cli_core.context import resolve_org, resolve_pod, selected_org, selected_pod
from ..cli_core.io import list_items, to_plain
from ..cli_core.state import CliState, build_state, client_session, update_config


def build_tui_state(*, config_file: Path, pod: str | None) -> CliState:
    state = build_state(
        config_file=config_file,
        server=None,
        base_url=None,
        auth_url=None,
        token=None,
        timeout=60.0,
        no_verify_ssl=False,
        output="pretty",
    )
    state.config.setdefault("_runtime", {})
    if pod:
        state.config["_runtime"]["pod"] = pod
    return state


def resolve_pod_id(state: CliState, pod: str | None = None) -> str | None:
    return selected_pod(state, pod, required=False)


def resolve_org_id(state: CliState) -> str | None:
    return selected_org(state, required=False)


def list_servers(state: CliState) -> list[dict[str, Any]]:
    root = state.root_config or {}
    return [
        {
            "id": name,
            "name": name,
            "base_url": get_server_config(root, name).get("base_url", ""),
            "active": name == state.server,
        }
        for name in server_names(root)
    ]


def list_orgs(state: CliState) -> list[dict[str, Any]]:
    with client_session(state) as client:
        return [to_plain(item) for item in list_items(client.orgs.list(limit=200))]


def list_pods(state: CliState) -> list[dict[str, Any]]:
    org_id = resolve_org_id(state)
    if not org_id:
        return []
    with client_session(state) as client:
        return [
            to_plain(item)
            for item in list_items(client.pods.list(org_id=org_id, limit=200))
        ]


def switch_server(state: CliState, name: str) -> str:
    """Activate a stored server; clears nothing else — each server keeps its own defaults."""
    if state.root_config is None:
        raise ValueError("No server config loaded.")
    server = normalize_server_name(name)
    if server not in (state.root_config.get("servers") or {}):
        raise ValueError(f"Server not found: {server}")
    state.root_config["active_server"] = server
    state.server = server
    state.config = get_server_config(state.root_config, server)
    state.server_source = "config"
    state.server_read_only = False
    state.config.setdefault("_runtime", {})
    save_config(state.config_path, state.root_config)
    return server


def select_org(state: CliState, selector: str) -> dict[str, Any]:
    """Persist the org default; clears the pod (a pod belongs to one org)."""
    with client_session(state) as client:
        org = to_plain(resolve_org(client, selector))
    org_id = str(org.get("id") or selector)

    def mutate(config: dict[str, Any]) -> None:
        defaults = config.setdefault("defaults", {})
        defaults["org_id"] = org_id
        defaults.pop("pod_id", None)

    update_config(state, mutate)
    runtime = state.config.setdefault("_runtime", {})
    runtime["org"] = org_id
    runtime.pop("pod", None)
    return org


def select_pod(state: CliState, selector: str) -> dict[str, Any]:
    """Persist the pod default (and its org when known)."""
    with client_session(state) as client:
        pod = to_plain(resolve_pod(client, state, selector, org=resolve_org_id(state)))
    pod_id = str(pod.get("id") or selector)
    org_id = pod.get("organization_id")

    def mutate(config: dict[str, Any]) -> None:
        defaults = config.setdefault("defaults", {})
        defaults["pod_id"] = pod_id
        if org_id:
            defaults["org_id"] = str(org_id)

    update_config(state, mutate)
    runtime = state.config.setdefault("_runtime", {})
    runtime["pod"] = pod_id
    if org_id:
        runtime["org"] = str(org_id)
    return pod
