from __future__ import annotations

import os
import shlex
from typing import TYPE_CHECKING, Any, Callable

from lemma_sdk.config import resolve_base_url
from lemma_sdk.errors import (
    LemmaAPIError,
    LemmaNotFoundError,
    LemmaPermissionError,
)

from .io import list_items, to_plain
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


#: One page of a name scan, and how many of them to walk. Bounded rather than
#: unbounded: the cost of `lemma --pod my-pod …` must not scale with how many
#: pods a mature org has, and past a thousand a name lookup is the wrong tool.
#: Matches `_MAX_POD_LOOKUP_PAGES` in commands/pods.py, which does the same walk
#: for the UUID case.
LOOKUP_PAGE_SIZE = 200
MAX_LOOKUP_PAGES = 5


def walk_lookup_pages(
    fetch: Callable[[str | None], object],
) -> tuple[list[dict[str, Any]], bool]:
    """Every item across at most `MAX_LOOKUP_PAGES` pages, and whether the walk
    stopped with more still to come.

    The second half is the point. A single page and a bare "not found" is a
    confident wrong answer — the caller can say how far it looked instead, which
    is the difference between a dead end and a hint.
    """
    items: list[dict[str, Any]] = []
    token: str | None = None
    for _page in range(MAX_LOOKUP_PAGES):
        response = fetch(token)
        items.extend(list_items(response))
        plain = to_plain(response)
        token = (
            (str(plain.get("next_page_token") or "") or None)
            if isinstance(plain, dict)
            else None
        )
        if not token:
            return items, False
    return items, True


def _matches(item: dict[str, Any], selector: str) -> bool:
    return selector in {
        str(item.get("id")),
        str(item.get("slug")),
        str(item.get("name")),
    }


def _searched_clause(count: int, truncated: bool) -> str:
    """Say what was actually looked at, when the walk did not see everything.

    A sentence appended to a complete message, not a parenthetical: "not found"
    and "not found in the part we looked at" are different answers, and the
    second one has an action attached.
    """
    if not truncated:
        return ""
    return f" Only the first {count} were searched — pass the id instead."


def resolve_org(client: Lemma, selector: str) -> dict[str, Any]:
    try:
        return client.orgs.get(selector).to_dict()
    except LemmaNotFoundError, LemmaPermissionError:
        # Only "it is not there / not yours" falls through to the slug scan. A
        # bare `except Exception` here turned an expired session, a 500 and a
        # dropped connection into "Organization not found", which sends the user
        # hunting for a typo that does not exist. Everything else propagates to
        # run_with_client, which names the status.
        pass
    orgs, truncated = walk_lookup_pages(
        lambda token: client.orgs.list(limit=LOOKUP_PAGE_SIZE, page_token=token)
    )
    for org in orgs:
        if _matches(org, selector):
            return org
    fail(f"Organization not found: {selector}.{_searched_clause(len(orgs), truncated)}")
    # fail() is NoReturn, but ruff's RET503 cannot see that; this line is for
    # the linter, not the reader.
    raise AssertionError("unreachable")


def resolve_pod(
    client: Lemma, state: CliState, selector: str, org: str | None = None
) -> dict[str, Any]:
    try:
        return client.pods.get(selector).to_dict()
    except LemmaNotFoundError, LemmaPermissionError:
        pass  # see resolve_org: only "not there / not yours" scans by slug

    org_id = org or selected_org(state, required=False)
    if not org_id:
        fail(
            "Pod lookup by slug needs an organization. Run `lemma orgs`, pass --org, or set LEMMA_ORG_ID."
        )
    # for_org is a view of this client -- same endpoint, credential and
    # connection pool. Constructing a second Lemma here opened a second pool
    # nobody closed and re-ran settings resolution, which can answer differently
    # from the client already in hand.
    scoped = client.for_org(str(org_id))
    pods, truncated = walk_lookup_pages(
        lambda token: scoped.pods.list(limit=LOOKUP_PAGE_SIZE, page_token=token)
    )
    for pod in pods:
        if _matches(pod, selector):
            return pod
    fail(pod_lookup_error(selector, state) + _searched_clause(len(pods), truncated))
    raise AssertionError("unreachable")  # see resolve_org: RET503 only


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
    raise AssertionError("unreachable")  # see resolve_org: RET503 only
