from __future__ import annotations

from datetime import datetime
from enum import Enum
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from app.modules.agent.domain.value_objects import (
    AgentRunApprovalDecision,
    JsonObject,
    JsonValue,
)
from app.modules.agent.tools.context import BaseToolResponse
from app.modules.datastore.contracts import RecordFilter


class DisplayResourceType(str, Enum):
    BROWSER = "BROWSER"
    FILE = "FILE"
    TABLE = "TABLE"
    AGENT = "AGENT"
    FUNCTION = "FUNCTION"
    WORKFLOW = "WORKFLOW"
    APP = "APP"
    SCHEDULE = "SCHEDULE"
    WIDGET = "WIDGET"

    @classmethod
    def _missing_(cls, value: object) -> "DisplayResourceType | None":
        if not isinstance(value, str):
            return None
        normalized = value.strip().upper()
        for member in cls:
            if member.value == normalized:
                return member
        return None


class DisplayResourceRequest(BaseModel):
    type: DisplayResourceType = Field(
        description="Kind of resource the user should see."
    )
    name: str | None = Field(
        default=None,
        min_length=1,
        description="Pod resource name; omit to show all of that type.",
    )
    path: str | None = Field(
        default=None,
        description="Pod file path, for FILE. Never a workspace path.",
    )
    public_url: str | None = Field(
        default=None, description="URL to embed, for WIDGET."
    )
    content: str | None = Field(
        default=None,
        description=(
            "Inline HTML fragment, for WIDGET. Raw markup only — never base64. "
            "No DOCTYPE/<html>/<head>/<body>. A standalone SVG image is a pod "
            "file shown with type=FILE, not a widget."
        ),
    )
    loading_messages: list[str] = Field(
        default_factory=list,
        max_length=4,
        description="Messages shown while a WIDGET renders.",
    )
    filters: list[RecordFilter] | None = Field(
        default=None,
        description="Record filters {field, op, value}, for TABLE.",
    )
    query: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "Read-only SQL for TABLE on RLS-disabled tables; prefer `filters`."
        ),
    )


def validate_display_payload(request: "DisplayResourceRequest") -> str | None:
    """Semantic validation for a ``display_resource`` request.

    Returns an error message when the payload is invalid for its ``type``, or
    ``None`` when valid. Deliberately NOT a raising pydantic ``model_validator``:
    keeping it out of argument validation lets ``display_resource`` surface a
    bad payload as the uniform ``success: false`` / ``error`` tool result (seen
    by both the model and the frontend) instead of a retry / validation error.
    """
    if request.type == DisplayResourceType.BROWSER:
        if (
            any(
                value is not None
                for value in (
                    request.name,
                    request.path,
                    request.public_url,
                    request.content,
                    request.filters,
                    request.query,
                )
            )
            or request.loading_messages
        ):
            return "BROWSER resources only accept type."
        return None

    if request.type != DisplayResourceType.FILE:
        if request.path is not None:
            return "path is only valid for FILE resources."

    if request.type != DisplayResourceType.WIDGET:
        if request.public_url is not None or request.content is not None:
            return "public_url and content are only valid for WIDGET resources."
        if request.loading_messages:
            return "loading_messages is only valid for WIDGET resources."
    if request.type == DisplayResourceType.FILE:
        if request.path is not None and request.path.startswith(
            ("/tmp/", "/private/", "/Users/")
        ):
            return (
                "FILE resources must reference pod-visible paths, not private "
                "workspace paths."
            )

    if request.type == DisplayResourceType.WIDGET:
        payload_count = sum(
            bool(value and value.strip())
            for value in (request.public_url, request.content)
        )
        if payload_count != 1:
            return "WIDGET resources must provide exactly one of public_url or content."
        if request.public_url:
            parsed = urlparse(request.public_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                return "WIDGET public_url must be an absolute http or https URL."

    if request.type != DisplayResourceType.TABLE and (
        request.filters is not None or request.query is not None
    ):
        return "filters and query are only valid for TABLE resources."

    if request.type == DisplayResourceType.TABLE:
        if request.filters is not None and request.query is not None:
            return "TABLE resources must not provide both filters and query."
        if request.filters is not None and request.name is None:
            return "TABLE filters require name to identify the table."

    return None


class DisplayResourceResponse(BaseToolResponse):
    app: str | None = Field(
        default=None,
        description="Displayed workspace app name when applicable.",
    )
    url: str | None = Field(
        default=None,
        description="Short-lived public URL for displayed workspace apps.",
    )
    expires_at: datetime | None = Field(
        default=None,
        description="ISO timestamp when the app access URL expires.",
    )


class UserApprovalResponse(BaseToolResponse):
    decision: AgentRunApprovalDecision | None = Field(
        default=None,
        description="User approval decision returned by the approval API.",
    )
    response: JsonObject = Field(
        default_factory=dict,
        description="Optional structured response submitted with the approval decision.",
    )


class RequestApprovalResponse(BaseToolResponse):
    """Result of a higher-order ``request_approval`` call.

    Carries both the user's decision and, when approved, the result of running
    the wrapped tool with the user's authority.
    """

    decision: AgentRunApprovalDecision | None = Field(
        default=None,
        description="The user's decision: APPROVE_ONCE, APPROVE_FOR_SESSION, or DENY.",
    )
    executed: bool = Field(
        default=False,
        description="Whether the wrapped tool was executed (true only when approved).",
    )
    result: JsonValue = Field(
        default=None,
        description="The wrapped tool's result when executed as the user.",
    )
    response: JsonObject = Field(
        default_factory=dict,
        description="Optional structured response submitted with the decision.",
    )
    interaction_fallback: bool = Field(
        default=False,
        description=(
            "True when a remote harness runtime could not pause and the model must "
            "ask "
            "for confirmation conversationally instead."
        ),
    )
    parked_tool_call_id: str | None = Field(
        default=None,
        description=(
            "Set when the decision is not in yet and the caller must wait for "
            "it. The Agent Host MCP bridge holds the tool response open and "
            "polls this id until the person decides, so the model sits inside "
            "its turn exactly as it does for its own native approvals."
        ),
    )


class AskUserOption(BaseModel):
    label: str = Field(description="The choice shown to the user (1-5 words).")
    description: str = Field(default="", description="One-line explanation.")
    recommended: bool = Field(
        default=False, description="Highlight this as your recommendation."
    )
    icon: str = Field(
        default="",
        max_length=8,
        description=(
            "Optional single emoji shown before the label (e.g. '📊'). "
            "Omit it when no glyph genuinely fits."
        ),
    )


class AskUserQuestion(BaseModel):
    question: str = Field(description="The full question.")
    header: str = Field(
        description="Few-word label, shown as a chip and used as the answer key."
    )
    options: list[AskUserOption] = Field(description="2-4 distinct choices.")
    multi_select: bool = Field(
        default=False, description="Allow selecting more than one option."
    )


class AskUserRequest(BaseModel):
    questions: list[AskUserQuestion] = Field(
        description=(
            "Questions to ask at once. An 'Other' free-text choice is always "
            "added for the user, so do not add one yourself."
        )
    )


class AskUserResponse(BaseToolResponse):
    """Result of an ``ask_user`` call: the user's answers to the questions."""

    answers: JsonObject = Field(
        default_factory=dict,
        description=(
            "The user's answers keyed by each question's header. Each value is the "
            "chosen option label(s) or the custom text they typed for 'Other'."
        ),
    )
    interaction_fallback: bool = Field(
        default=False,
        description=(
            "True when a remote harness runtime could not pause and the model must "
            "ask "
            "the question conversationally instead."
        ),
    )
    parked_tool_call_id: str | None = Field(
        default=None,
        description=(
            "Set when the answer is not ready yet and the caller must wait for "
            "it. The Agent Host MCP bridge holds the tool response open and "
            "polls this id until the person decides, so the model sits inside "
            "its turn exactly as it does for its own native approvals."
        ),
    )
