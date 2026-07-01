from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote, urlencode
from uuid import UUID

from app.core.config import settings
from app.core.log.log import get_logger
from app.modules.agent_surfaces.platforms.rendering import sanitize_user_visible_text
from app.modules.agent.tools.user_interaction.models import (
    AskUserRequest,
    DisplayResourceRequest,
    DisplayResourceType,
)
from app.modules.agent_surfaces.domain.models import (
    OTHER_ANSWER_SUFFIX,
    SurfaceDisplayAction,
    SurfaceDisplayRenderPlan,
    SurfaceQuestion,
    SurfaceQuestionOption,
    SurfaceQuestionRenderPlan,
)

logger = get_logger(__name__)

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
    title = (
        questions[0].question
        if len(questions) == 1
        else "A few quick questions"
    )
    return SurfaceQuestionRenderPlan(
        title=title,
        questions=questions,
        callback_id=build_callback_id(conversation_id, tool_call_id),
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
    """Render an ask_user plan as a well-formatted chat message.

    Used as the fallback on platforms without native tappable choices (and on
    any platform where the native render is unavailable). The user replies with
    a number/label or free text, which the ingress routes back as the answer.
    """
    blocks: list[str] = []
    multiple = len(plan.questions) > 1
    for index, question in enumerate(plan.questions, start=1):
        header = f"{index}. {question.question}" if multiple else question.question
        lines = [header]
        for opt_index, option in enumerate(question.options, start=1):
            suffix = " (recommended)" if option.recommended else ""
            detail = f" — {option.description}" if option.description else ""
            lines.append(f"  {opt_index}. {option.label}{detail}{suffix}")
        blocks.append("\n".join(lines))
    prompt = "Reply with your choice"
    if any(q.multi_select for q in plan.questions):
        prompt += " (you can pick more than one)"
    prompt += ", or type your own answer."
    return "\n\n".join(blocks + [prompt])


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
        return _append_tool_context(
            f"{pod_base}/widgets/view",
            conversation_id=conversation_id,
            tool_call_id=tool_call_id,
        )
    if request.type is DisplayResourceType.FILE:
        return _file_resource_url(pod_base, request, conversation_id)
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
        return _append_conversation(
            f"{pod_base}/app/view?{urlencode({'page': request.name})}"
            if request.name
            else f"{pod_base}/app/pages",
            conversation_id,
        )
    if request.type is DisplayResourceType.SCHEDULE:
        return _append_conversation(
            f"{pod_base}/schedules?{urlencode({'target': request.name})}"
            if request.name
            else f"{pod_base}/schedules",
            conversation_id,
        )
    return _conversation_url(pod_base, conversation_id, tool_call_id)


def _display_resource_title(request: DisplayResourceRequest) -> str:
    name = request.name or request.path
    kind = _display_resource_kind(request)
    if name:
        return f"{kind}: {name}"
    if request.type is DisplayResourceType.BROWSER:
        return "Browser ready"
    return f"{kind} ready"


def _display_resource_summary(request: DisplayResourceRequest) -> str | None:
    if request.type is DisplayResourceType.TABLE and request.query:
        return "Read-only query results are ready in Lemma."
    if request.type is DisplayResourceType.TABLE:
        return "A datastore view is ready."
    if request.type is DisplayResourceType.WIDGET:
        return "An interactive widget is ready."
    if request.type is DisplayResourceType.FILE:
        return "A file is ready to inspect."
    if request.type is DisplayResourceType.BROWSER:
        return "The live browser view is ready."
    return "A Lemma resource is ready."


def _display_resource_detail_lines(
    request: DisplayResourceRequest,
    *,
    tool_output: object | None,
) -> list[str]:
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
    if request.type is DisplayResourceType.WIDGET:
        # Do not leak a raw serve/source URL to a surface — the action button
        # already carries the user-facing /widgets/view deep link.
        return ["Interactive widget"]
    if request.type is DisplayResourceType.BROWSER:
        output = _as_record(tool_output)
        expires_at = _as_nonempty_string(output.get("expires_at"))
        return [f"Expires: {expires_at}"] if expires_at else []
    return []


def _display_resource_action_label(request: DisplayResourceRequest) -> str:
    if request.type is DisplayResourceType.WIDGET:
        return "Open widget"
    if request.type is DisplayResourceType.FILE:
        return "Open file"
    if request.type is DisplayResourceType.TABLE:
        return "Open in Lemma"
    if request.type is DisplayResourceType.BROWSER:
        return "Open browser"
    return "Open resource"


def _display_resource_kind(request: DisplayResourceRequest) -> str:
    return request.type.value.lower().replace("_", " ").title()


def _file_resource_url(
    pod_base: str,
    request: DisplayResourceRequest,
    conversation_id: UUID | None,
) -> str:
    if not request.path:
        return _append_conversation(f"{pod_base}/files", conversation_id)
    file_path = _normalize_pod_file_path(request.path)
    params: dict[str, str] = {"file": file_path}
    parent = _parent_path(file_path)
    if parent:
        params["folder"] = parent
    return _append_conversation(
        f"{pod_base}/files?{urlencode(params)}", conversation_id
    )


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


def _parent_path(path: str) -> str | None:
    normalized = path.rstrip("/")
    parts = [part for part in normalized.split("/") if part]
    if len(parts) <= 1:
        return None
    return "/" + "/".join(parts[:-1])


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
