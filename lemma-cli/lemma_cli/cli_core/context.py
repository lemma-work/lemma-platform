from __future__ import annotations

import os
import shlex
from typing import TYPE_CHECKING, Any

from lemma_sdk.config import resolve_base_url
from lemma_sdk.errors import LemmaAPIError

from .io import list_items
from .state import CliState, console, fail, humanize_error, update_config

if TYPE_CHECKING:
    from lemma_sdk import Lemma


def _defaults(state: CliState) -> dict[str, Any]:
    defaults = state.config.setdefault("defaults", {})
    if not isinstance(defaults, dict):
        state.config["defaults"] = {}
        return state.config["defaults"]
    return defaults


def selected_org(
    state: CliState, explicit: str | None = None, *, required: bool = True
) -> str | None:
    runtime = (
        state.config.get("_runtime")
        if isinstance(state.config.get("_runtime"), dict)
        else {}
    )
    value = (
        explicit
        or runtime.get("org")
        or os.getenv("LEMMA_ORG_ID")
        or _defaults(state).get("org_id")
    )
    if required and not value:
        fail(
            "No organization selected. Run `lemma orgs`, pass --org, or set LEMMA_ORG_ID."
        )
    return str(value) if value else None


def selected_pod(
    state: CliState, explicit: str | None = None, *, required: bool = True
) -> str | None:
    runtime = (
        state.config.get("_runtime")
        if isinstance(state.config.get("_runtime"), dict)
        else {}
    )
    value = (
        explicit
        or runtime.get("pod")
        or os.getenv("LEMMA_POD_ID")
        or _defaults(state).get("pod_id")
    )
    if required and not value:
        fail(
            f"No pod bound for server '{state.server}'. Pass --pod, set LEMMA_POD_ID, "
            f"or create .lemma.{state.server}.env (run `lemma pods select` or `lemma app init`)."
        )
    return str(value) if value else None


def selected_conversation(
    state: CliState, explicit: str | None = None, *, required: bool = False
) -> str | None:
    runtime = (
        state.config.get("_runtime")
        if isinstance(state.config.get("_runtime"), dict)
        else {}
    )
    value = (
        explicit
        or runtime.get("conversation")
        or os.getenv("LEMMA_CONVERSATION_ID")
        or _defaults(state).get("conversation_id")
    )
    if required and not value:
        fail(
            "No conversation selected. Pass --conversation or set LEMMA_CONVERSATION_ID."
        )
    return str(value) if value else None


def remember_org(
    state: CliState, org_id: str, *, clear_pod: bool = False
) -> dict[str, Any]:
    def mutate(config: dict[str, Any]) -> None:
        defaults = config.setdefault("defaults", {})
        previous_org = defaults.get("org_id")
        defaults["org_id"] = org_id
        if clear_pod and previous_org != org_id:
            defaults.pop("pod_id", None)

    return update_config(state, mutate)


def remember_pod(state: CliState, pod_id: str) -> dict[str, Any]:
    return update_config(
        state,
        lambda config: config.setdefault("defaults", {}).update({"pod_id": pod_id}),
    )


def remember_conversation(
    state: CliState, conversation_id: str | None
) -> dict[str, Any]:
    def mutate(config: dict[str, Any]) -> None:
        defaults = config.setdefault("defaults", {})
        if conversation_id:
            defaults["conversation_id"] = conversation_id
        else:
            defaults.pop("conversation_id", None)

    return update_config(state, mutate)


def render_session_selection(
    state: CliState,
    *,
    env: dict[str, str],
    label: str,
    name: str,
    command_hint: str,
    export_only: bool,
    saved: bool,
) -> None:
    """Render a session-scoped `select`: the active org/pod lives in the shell's
    env, so a `select` never mutates the shared config (unless --save-default).

    In ``-x`` (export) mode print ONLY the ``export …`` lines so the user can
    ``eval "$(lemma … select X -x)"``; otherwise print a human summary plus the
    eval one-liner. JSON output mode emits a structured payload for scripts.
    """
    exports = [
        f"export {key}={shlex.quote(value)}" for key, value in env.items() if value
    ]
    if export_only:
        for line in exports:
            print(line)  # noqa: T201 — eval-safe stdout, no rich markup
        return
    if state.output == "json":
        from .io import emit

        emit(state, {"selected": name, "env": env, "saved_default": saved})
        return
    console.print(
        f"[green]{label}[/green] [bold]{name}[/bold] [dim]— active for this shell only[/dim]"
    )
    if saved:
        console.print(f"[dim]also saved as this server's default {label}.[/dim]")
    console.print("[dim]apply to your shell:[/dim] " + f'eval "$({command_hint} -x)"')
    for line in exports:
        console.print(f"  [dim]{line}[/dim]")


def resolve_org(client: Lemma, selector: str) -> dict[str, Any]:
    try:
        return client.orgs.get(selector).to_dict()
    except Exception:
        pass
    for org in list_items(client.orgs.list(limit=200)):
        if selector in {str(org.get("id")), str(org.get("slug")), str(org.get("name"))}:
            return org
    fail(f"Organization not found: {selector}")
    raise AssertionError("unreachable")


def resolve_pod(
    client: Lemma, state: CliState, selector: str, org: str | None = None
) -> dict[str, Any]:
    try:
        return client.pods.get(selector).to_dict()
    except Exception:
        pass
    from lemma_sdk import Lemma

    org_id = org or selected_org(state, required=False)
    if not org_id:
        fail(
            "Pod lookup by slug needs an organization. Run `lemma orgs`, pass --org, or set LEMMA_ORG_ID."
        )
    scoped = Lemma(
        base_url=client.settings.base_url,
        token=client.settings.token,
        org_id=org_id,
        timeout=client.settings.timeout,
        verify_ssl=client.settings.verify_ssl,
    )
    try:
        pods = scoped.pods.list(limit=200)
    finally:
        scoped.close()
    for pod in list_items(pods):
        if selector in {str(pod.get("id")), str(pod.get("slug")), str(pod.get("name"))}:
            return pod
    fail(pod_lookup_error(selector, state))
    raise AssertionError("unreachable")


def pod_lookup_error(pod_id: str, state: CliState, exc: Exception | None = None) -> str:
    """A pod-not-found/forbidden message that names the server, so a folder pointed
    at the wrong server (or a stale ``LEMMA_POD_ID``) is obvious. Non 403/404 API
    errors fall through to the normal humanized message."""
    status = getattr(exc, "status_code", None) if exc is not None else None
    if exc is not None and status not in (None, 403, 404):
        return humanize_error(exc)
    base = resolve_base_url(
        state.base_url, state.config, use_env=state.server_source == "env"
    )
    return (
        f"Pod '{pod_id}' not found on server '{state.server}' ({base}). "
        "It may not exist there, or you may be pointed at the wrong server. "
        "Check LEMMA_POD_ID / LEMMA_SERVER (or .lemma.env), or run `lemma pods list`."
    )


def org_for(client: Lemma, state: CliState, explicit: str | None = None) -> str:
    """Resolve the organization, deriving it from the selected pod when needed.

    Precedence: ``--org`` / runtime / ``LEMMA_ORG_ID`` / config default
    (via :func:`selected_org`), else a ``pods.get`` on the selected pod. Lets a
    folder that only pins ``LEMMA_POD_ID`` still run org-scoped commands. The
    resolved org is cached into runtime so later ``selected_org`` calls see it.
    """
    org = selected_org(state, explicit, required=False)
    if org:
        return org
    pod = selected_pod(state, required=False)
    if pod:
        try:
            info = client.pods.get(pod).to_dict()
        except LemmaAPIError as exc:
            fail(pod_lookup_error(pod, state, exc))
        org = info.get("organization_id") or info.get("org_id")
        if org:
            runtime = state.config.setdefault("_runtime", {})
            if isinstance(runtime, dict):
                runtime["org"] = str(org)
            return str(org)
    fail(
        "No organization selected. Pass --org, set LEMMA_ORG_ID, or target a pod "
        "whose org can be resolved."
    )
    raise AssertionError("unreachable")
