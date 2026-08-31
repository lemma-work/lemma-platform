from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote, urlencode
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


def render_questions_as_text(plan: SurfaceQuestionRenderPlan) -> str:
    """Deprecated alias for ``SurfaceQuestionRenderPlan.to_plain_text``.

    The degradation moved onto the plan so a delivery can ask any part for its
    text without knowing which part it is holding.
    """
    return plan.to_plain_text()


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

    NOTE: this is the single place that encodes lemma-os frontend route shapes
    (``/widgets/view``, ``/data``, ``/agents/{name}``, ``/app/view``,
    ``/schedules``, ``/conversations/{id}``, …). These MUST stay in sync with the
    frontend router; changing a route there without updating here produces dead
    links. Keep all route construction in this function so the contract is
    auditable in one place. (Verify ``/widgets/view`` and ``/app/view`` against
    the current lemma-os routes — they changed with the widget/app unification +
    host-based app routing.)
    """
    if request.type is DisplayResourceType.BROWSER:
        output = _as_record(tool_output)
        return _as_nonempty_string(output.get("url"))

    base = settings.frontend_url.rstrip("/")
    pod_base = f"{base}/pod/{quote(str(pod_id), safe='')}"

    if request.type is DisplayResourceType.WIDGET:
        return _widget_resource_url(
            pod_base,
            request,
            conversation_id=conversation_id,
            tool_call_id=tool_call_id,
        )
    if request.type is DisplayResourceType.FILE:
        return _file_resource_url(pod_base, request)
    if request.type is DisplayResourceType.TABLE:
        if request.query:
            return _conversation_url(pod_base, conversation_id, tool_call_id)
        return _table_resource_url(pod_base, request, conversation_id)
    if request.type is DisplayResourceType.AGENT:
        return _append_conversation(
            f"{pod_base}/agents/{quote(request.name, safe='')}"
            if request.name
            else f"{pod_base}/ai",
            conversation_id,
        )
    if request.type is DisplayResourceType.FUNCTION:
        return _append_conversation(
            f"{pod_base}/functions/{quote(request.name, safe='')}"
            if request.name
            else f"{pod_base}/functions",
            conversation_id,
        )
    if request.type is DisplayResourceType.WORKFLOW:
        return _append_conversation(
            f"{pod_base}/flows/{quote(request.name, safe='')}"
            if request.name
            else f"{pod_base}/flows",
            conversation_id,
        )
    if request.type is DisplayResourceType.APP:
        # `page` carries the app's resource name, not a slug, on purpose. The
        # workspace addresses an app page by the slug of its name and
        # canonicalizes whatever the link carries on arrival, so the name
        # resolves. Slugifying here instead would need this module to reproduce
        # the frontend's slug rule exactly -- `normalize_public_slug` does not
        # (`Ledger 2.0` -> `ledger-2-0` there, `ledger20` in the index) -- and a
        # slug built by the wrong rule is one the workspace cannot resolve back.
        return _append_conversation(
            f"{pod_base}/app/view?{urlencode({'page': request.name})}"
            if request.name
            else f"{pod_base}/app/pages",
            conversation_id,
        )
    if request.type is DisplayResourceType.SCHEDULE:
        return _append_conversation(
            f"{pod_base}/schedules",
            conversation_id,
        )
    return _conversation_url(pod_base, conversation_id, tool_call_id)


def _widget_resource_url(
    pod_base: str,
    request: DisplayResourceRequest,
    *,
    conversation_id: UUID | None,
    tool_call_id: str | None,
) -> str:
    return request.public_url or _append_tool_context(
        f"{pod_base}/widgets/view",
        conversation_id=conversation_id,
        tool_call_id=tool_call_id,
    )


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


def _file_resource_url(pod_base: str, request: DisplayResourceRequest) -> str:
    # A bare ``?file=<path>`` deep link is intentional. The in-app document
    # viewer opens the file straight from the path and derives the parent folder
    # from it, so ``folder`` is redundant. ``assistantConversationId`` is
    # deliberately omitted too: when present it switches the viewer into a
    # header-less "assistant presentation" mode, whereas the plain path opens the
    # full file viewer (with header + back nav) — the better reading experience.
    if not request.path:
        return f"{pod_base}/files"
    file_path = _normalize_pod_file_path(request.path)
    return f"{pod_base}/files?{urlencode({'file': file_path})}"


def _table_resource_url(
    pod_base: str,
    request: DisplayResourceRequest,
    conversation_id: UUID | None,
) -> str:
    href = (
        f"{pod_base}/data?{urlencode({'tab': request.name})}"
        if request.name
        else f"{pod_base}/data"
    )
    if request.filters:
        href = _append_repeated_params(
            href,
            [
                (
                    "filter",
                    json.dumps(
                        item.model_dump(mode="json", exclude_none=True),
                        separators=(",", ":"),
                    ),
                )
                for item in request.filters
            ],
        )
    return _append_conversation(href, conversation_id)


def _conversation_url(
    pod_base: str,
    conversation_id: UUID | None,
    tool_call_id: str | None,
) -> str | None:
    if conversation_id is None:
        return None
    return _append_query(
        f"{pod_base}/conversations/{quote(str(conversation_id), safe='')}",
        {"toolCallId": tool_call_id},
    )


def _append_tool_context(
    href: str,
    *,
    conversation_id: UUID | None,
    tool_call_id: str | None,
) -> str:
    return _append_query(
        href,
        {
            "toolCallId": tool_call_id,
            "assistantConversationId": str(conversation_id)
            if conversation_id
            else None,
        },
    )


def _append_conversation(href: str, conversation_id: UUID | None) -> str:
    return _append_query(
        href,
        {"assistantConversationId": str(conversation_id) if conversation_id else None},
    )


def _append_repeated_params(href: str, params: list[tuple[str, str]]) -> str:
    if not params:
        return href
    separator = "&" if "?" in href else "?"
    return href + separator + urlencode(params)


def _append_query(href: str, params: dict[str, str | None]) -> str:
    cleaned = {key: value for key, value in params.items() if value}
    if not cleaned:
        return href
    separator = "&" if "?" in href else "?"
    return href + separator + urlencode(cleaned)


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
