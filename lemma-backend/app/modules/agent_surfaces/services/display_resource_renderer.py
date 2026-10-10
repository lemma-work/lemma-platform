from __future__ import annotations

from typing import Any
from urllib.parse import quote
from uuid import UUID

from app.core.config import settings
from app.modules.agent_surfaces.platforms.rendering import sanitize_user_visible_text
from app.modules.agent.contracts import (
    AskUserRequest,
    DisplayResourceRequest,
    DisplayResourceType,
)
from app.modules.agent_surfaces.domain.models import (
    APPROVAL_DECISION_APPROVE,
    APPROVAL_DECISION_DENY,
    APPROVAL_DECISION_SESSION,
    OTHER_ANSWER_SUFFIX,
    SurfaceApprovalButton,
    SurfaceApprovalRenderPlan,
    SurfaceDisplayAction,
    SurfaceDisplayRenderPlan,
    SurfaceQuestion,
    SurfaceQuestionOption,
    SurfaceQuestionRenderPlan,
)

# Separates conversation id and tool_call id inside an interaction callback id so
# an inbound interaction (e.g. an ask_user answer) can be routed back to the
# waiting agent run.
CALLBACK_SEPARATOR = "|"


def build_callback_id(conversation_id: UUID, tool_call_id: str) -> str:
    return f"{conversation_id}{CALLBACK_SEPARATOR}{tool_call_id}"


def parse_callback_id(callback_id: str) -> tuple[str, str] | None:
    """Return (conversation_id, tool_call_id) from a callback id, or None."""
    raw = str(callback_id or "")
    if CALLBACK_SEPARATOR not in raw:
        return None
    conversation_id, tool_call_id = raw.split(CALLBACK_SEPARATOR, 1)
    if not conversation_id or not tool_call_id:
        return None
    return conversation_id, tool_call_id


def build_ask_user_render_plan(
    *,
    request: AskUserRequest,
    conversation_id: UUID,
    tool_call_id: str,
) -> SurfaceQuestionRenderPlan:
    """Build a platform-neutral plan for rendering ``ask_user`` questions.

    Each question's ``header`` is the answer key, so a native submission keyed by
    header maps straight into ``AskUserResponse.answers``. ``callback_id`` routes
    the submission back to the waiting run.
    """
    # Sanitize every model-authored string so reasoning never reaches a user,
    # regardless of which adapter renders it (blocks/cards/keyboards/text).
    questions = [
        SurfaceQuestion(
            header=q.header,
            question=sanitize_user_visible_text(q.question),
            options=[
                SurfaceQuestionOption(
                    label=sanitize_user_visible_text(o.label),
                    description=sanitize_user_visible_text(o.description)
                    if o.description
                    else o.description,
                    recommended=o.recommended,
                )
                for o in q.options
            ],
            multi_select=q.multi_select,
        )
        for q in request.questions
    ]
    title = questions[0].question if len(questions) == 1 else "A few quick questions"
    return SurfaceQuestionRenderPlan(
        title=title,
        questions=questions,
        callback_id=build_callback_id(conversation_id, tool_call_id),
    )


def build_approval_render_plan(
    *,
    conversation_id: UUID,
    tool_call_id: str,
    title: str,
    reason: str | None,
    tool_name: str | None,
    allow_session: bool = False,
) -> SurfaceApprovalRenderPlan:
    """Build a platform-neutral plan for rendering a ``request_approval`` prompt.

    Always includes Approve + Deny; the approve-for-session button is added only
    when ``allow_session`` is set (i.e. the paused call carries a real permission
    gate). ``callback_id`` routes the tapped decision back to the waiting run.
    """
    clean_title = sanitize_user_visible_text(title) or "Action requires your approval"
    clean_reason = sanitize_user_visible_text(reason).strip() if reason else None
    clean_tool = sanitize_user_visible_text(tool_name).strip() if tool_name else None
    buttons = [
        SurfaceApprovalButton(
            label="Approve", decision=APPROVAL_DECISION_APPROVE, style="primary"
        ),
        SurfaceApprovalButton(
            label="Deny", decision=APPROVAL_DECISION_DENY, style="danger"
        ),
    ]
    if allow_session:
        buttons.append(
            SurfaceApprovalButton(
                label="Approve for session", decision=APPROVAL_DECISION_SESSION
            )
        )
    return SurfaceApprovalRenderPlan(
        title=clean_title,
        reason=clean_reason or None,
        action_summary=clean_tool or None,
        callback_id=build_callback_id(conversation_id, tool_call_id),
        buttons=buttons,
    )


def merge_other_answers(values: dict[str, Any]) -> dict[str, Any]:
    """Fold native "Other" free-text inputs into their question's answer.

    Native renders add an optional ``{header}__other`` text input per question;
    when filled, the typed text overrides the selected option. Unanswered
    (empty) values are dropped so ``AskUserResponse.answers`` only carries real
    answers keyed by question header.
    """
    merged: dict[str, Any] = {}
    others: dict[str, str] = {}
    for key, value in (values or {}).items():
        if key.endswith(OTHER_ANSWER_SUFFIX):
            header = key[: -len(OTHER_ANSWER_SUFFIX)]
            text = str(value).strip() if value is not None else ""
            if text:
                others[header] = text
        else:
            merged[key] = value
    merged.update(others)
    return {k: v for k, v in merged.items() if v not in (None, "", [])}


def build_display_resource_render_plan(
    *,
    pod_id: UUID,
    request: DisplayResourceRequest,
    conversation_id: UUID | None = None,
    tool_call_id: str | None = None,
    tool_output: object | None = None,
) -> SurfaceDisplayRenderPlan:
    title = sanitize_user_visible_text(_display_resource_title(request))
    summary_raw = _display_resource_summary(request)
    summary = sanitize_user_visible_text(summary_raw) if summary_raw else summary_raw
    detail_lines = [
        sanitize_user_visible_text(line)
        for line in _display_resource_detail_lines(request, tool_output=tool_output)
    ]
    url = build_display_resource_url(
        pod_id=pod_id,
        request=request,
        conversation_id=conversation_id,
        tool_call_id=tool_call_id,
        tool_output=tool_output,
    )
    actions = (
        [SurfaceDisplayAction(label=_display_resource_action_label(request), url=url)]
        if url
        else []
    )
    return SurfaceDisplayRenderPlan(
        resource_type=request.type.value,
        title=title,
        summary=summary,
        detail_lines=detail_lines,
        actions=actions,
        tool_call_id=tool_call_id,
        request=request.model_dump(mode="json", exclude_none=True),
    )


def build_display_resource_url(
    *,
    pod_id: UUID,
    request: DisplayResourceRequest,
    conversation_id: UUID | None = None,
    tool_call_id: str | None = None,
    tool_output: object | None = None,
) -> str | None:
    """Build the deep link a surface user follows to open a resource in Lemma.

    The one place backend code spells the workspace's routes -- `/t/{pod}/...`,
    read by `lemma-frontend/src/shell/address.ts`. A route changed there and not
    here is a dead link in every chat app, so keep all of them in this function.

    A resource that was only ever drawn in a conversation -- an inline widget, a
    filtered or queried table -- links to that conversation, because there is
    no other page that shows the same thing. ``tool_call_id`` stays in the
    signature for callers; no current route can scroll to one call.
    """
    del tool_call_id
    if request.type is DisplayResourceType.BROWSER:
        output = _as_record(tool_output)
        return _as_nonempty_string(output.get("url"))

    base = settings.frontend_url.rstrip("/")
    space = f"{base}/t/{quote(str(pod_id), safe='')}"
    conversation = (
        f"{space}/conversation/{quote(str(conversation_id), safe='')}"
        if conversation_id is not None
        else None
    )
    name = quote(request.name, safe="") if request.name else None

    match request.type:
        case DisplayResourceType.WIDGET:
            return request.public_url or conversation or space
        case DisplayResourceType.FILE:
            return _file_resource_url(space, request)
        case DisplayResourceType.TABLE:
            if (request.query or request.filters) and conversation:
                return conversation
            return f"{space}/table/{name}" if name else f"{space}/tables"
        case DisplayResourceType.AGENT:
            return f"{space}/profile/{name}" if name else f"{space}/about"
        case DisplayResourceType.WORKFLOW:
            return f"{space}/workflow/{name}" if name else f"{space}/workflows"
        case DisplayResourceType.APP:
            # The app's resource name, not a slug, on purpose: the workspace
            # canonicalizes whatever the link carries on arrival, and a slug
            # built here by a different rule is one it cannot resolve back.
            return f"{space}/app/{name}" if name else f"{space}/apps"
        case DisplayResourceType.SCHEDULE:
            return f"{space}/about?section=schedules"
        case _:
            # A function has no page of its own in the workspace: the
            # conversation that showed it, else the teammate's space.
            return conversation or space


def _display_resource_title(request: DisplayResourceRequest) -> str:
    """The card's headline: what this resource is *called*.

    A FILE is titled by its file name alone. The full pod path used to be the
    title, which on a chat surface is a line of directory noise where the
    reader is looking for a name -- and it is also the caption stamped onto the
    native attachment, so the ugliness survived even when delivery worked. The
    folder is still shown, as a detail line, where a path belongs.
    """
    if request.type is DisplayResourceType.FILE:
        return _file_display_name(request.path) or "Files"
    if request.type is DisplayResourceType.TABLE and request.query:
        return "Query results"
    kind = _display_resource_kind(request)
    if request.name:
        return f"{kind}: {request.name}"
    if request.type is DisplayResourceType.BROWSER:
        return "Browser ready"
    return f"{kind} ready"


def _file_display_name(path: str | None) -> str | None:
    """The last segment of a pod file path, e.g. ``q3-report.pdf``."""
    trimmed = str(path or "").replace("\\", "/").rstrip("/")
    if not trimmed:
        return None
    return trimmed.rsplit("/", 1)[-1] or None


def _file_display_folder(path: str | None) -> str | None:
    """The directory a file sits in, or None at the root."""
    trimmed = str(path or "").replace("\\", "/").rstrip("/")
    if "/" not in trimmed:
        return None
    folder = trimmed.rsplit("/", 1)[0]
    return folder or None


# What each kind of resource is, said once, in the person's terms. These are
# the fallbacks: when the surface can resolve the resource for real it replaces
# them with something that counts (a file's size, a table's row count). A line
# that says only "A datastore view is ready." is the shape of an answer without
# the answer in it, so nothing here is allowed to be that.
_SUMMARY_BY_TYPE: dict[DisplayResourceType, str] = {
    DisplayResourceType.BROWSER: "A live browser you can watch and take over.",
    DisplayResourceType.AGENT: "An agent in this pod.",
    DisplayResourceType.FUNCTION: "A function in this pod.",
    DisplayResourceType.WORKFLOW: "A workflow in this pod.",
    DisplayResourceType.APP: "An app in this pod.",
    DisplayResourceType.SCHEDULE: "A schedule in this pod.",
}


def _display_resource_summary(request: DisplayResourceRequest) -> str | None:
    if request.fallback:
        # The agent's own words for what the resource shows beat any sentence
        # about where it opens: a chat app cannot draw a widget, and "Opens in
        # Lemma" told the person nothing they could act on without leaving.
        lines = sanitize_user_visible_text(request.fallback).splitlines()
        return "\n".join(" ".join(line.split()) for line in lines if line.strip())
    if request.type is DisplayResourceType.TABLE:
        return None if request.query else _table_summary(request)
    if request.type is DisplayResourceType.WIDGET:
        # A widget is HTML, and no chat surface can render HTML. Saying where it
        # does open is the only useful thing left; "Widget" under the heading
        # "Widget ready" was the card repeating itself.
        return "Opens in your browser." if request.public_url else "Opens in Lemma."
    if request.type is DisplayResourceType.FILE:
        # Replaced by the real thing (kind + size) once the file resolves; a
        # bare file name on its own line is already a decent fallback.
        return None
    return _SUMMARY_BY_TYPE.get(request.type)


def _table_summary(request: DisplayResourceRequest) -> str | None:
    if not request.name:
        return "Every table in this pod."
    return None


def _display_resource_detail_lines(
    request: DisplayResourceRequest,
    *,
    tool_output: object | None,
) -> list[str]:
    if request.type is DisplayResourceType.FILE:
        folder = _file_display_folder(request.path)
        return [f"In {folder}"] if folder else []
    if request.type is DisplayResourceType.TABLE:
        if request.query:
            return [f"Query: {_compact(request.query, 240)}"]
        if request.filters:
            return [
                "Filters: "
                + "; ".join(
                    _compact(
                        f"{item.field} {item.op.value if hasattr(item.op, 'value') else item.op} {item.value}",
                        80,
                    )
                    for item in request.filters[:5]
                )
            ]
    if request.type is DisplayResourceType.BROWSER:
        output = _as_record(tool_output)
        expires_at = _as_nonempty_string(output.get("expires_at"))
        return [f"Expires: {expires_at}"] if expires_at else []
    return []


# The button says what it opens. "Open resource" was the label on every card
# but four, and a button naming a category nobody outside Lemma uses is one
# nobody presses.
_ACTION_LABEL_BY_TYPE: dict[DisplayResourceType, str] = {
    DisplayResourceType.WIDGET: "Open widget",
    DisplayResourceType.FILE: "Open file",
    DisplayResourceType.TABLE: "Open in Lemma",
    DisplayResourceType.BROWSER: "Open browser",
    DisplayResourceType.AGENT: "Open agent",
    DisplayResourceType.FUNCTION: "Open function",
    DisplayResourceType.WORKFLOW: "Open workflow",
    DisplayResourceType.APP: "Open app",
    DisplayResourceType.SCHEDULE: "Open schedules",
}


def _display_resource_action_label(request: DisplayResourceRequest) -> str:
    return _ACTION_LABEL_BY_TYPE.get(request.type, "Open in Lemma")


def _display_resource_kind(request: DisplayResourceRequest) -> str:
    return request.type.value.lower().replace("_", " ").title()


def _file_resource_url(space: str, request: DisplayResourceRequest) -> str:
    """`/t/{pod}/file/<path>`, each segment encoded on its own: the slashes in a
    pod path are structure, and everything else in a file name -- a `#`, a `?`,
    a space -- must not reach the URL raw."""
    if not request.path:
        return f"{space}/files"
    segments = [
        quote(segment, safe="")
        for segment in _normalize_pod_file_path(request.path).split("/")
        if segment
    ]
    return f"{space}/file/{'/'.join(segments)}" if segments else f"{space}/files"


def _normalize_pod_file_path(path: str) -> str:
    normalized = path.replace("\\", "/").strip()
    while "//" in normalized:
        normalized = normalized.replace("//", "/")
    with_leading = normalized if normalized.startswith("/") else f"/{normalized}"
    if with_leading == "/pod":
        return "/"
    if with_leading.startswith("/pod/"):
        return with_leading[len("/pod") :] or "/"
    return with_leading


def _compact(value: object, max_length: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= max_length:
        return text
    return text[: max_length - 1].rstrip() + "..."


def _as_record(value: object | None) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")  # type: ignore[union-attr]
    return value if isinstance(value, dict) else {}


def _as_nonempty_string(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None
