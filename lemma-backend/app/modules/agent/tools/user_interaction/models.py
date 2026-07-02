from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from app.modules.agent.domain.value_objects import (
    AgentRunApprovalDecision,
    JsonObject,
    JsonValue,
)
from app.modules.agent.tools.context import BaseToolResponse
from app.modules.datastore.api.schemas.datastore_schemas import RecordFilter


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
        description=(
            "Unique pod resource name within the selected type. Omit name to display "
            "all resources of that type."
        ),
    )
    path: str | None = Field(
        default=None,
        description=(
            "Full pod-visible file path for FILE resources. Do not pass private "
            "workspace paths."
        ),
    )
    public_url: str | None = Field(
        default=None,
        description="Public URL to embed or open for WIDGET resources.",
    )
    content: str | None = Field(
        default=None,
        description=(
            "Inline renderable content for WIDGET resources, such as raw SVG or HTML. "
            "Do not include DOCTYPE, <html>, <head>, or <body> tags."
        ),
    )
    loading_messages: list[str] = Field(
        default_factory=list,
        max_length=4,
        description="For WIDGET resources, optional loading messages shown while the visual renders.",
    )
    interactive: bool = Field(
        default=False,
        description=(
            "For WIDGET resources, set true when the widget collects input and "
            "submits it back to the chat (via the in-widget lemma.submit(payload) / "
            "sendPrompt(text) bridge). Use an interactive widget instead of ask_user "
            "when you need richer or free-form structured input."
        ),
    )
    filters: list[RecordFilter] | None = Field(
        default=None,
        description=(
            "For TABLE resources, optional record filters using the datastore "
            "record API shape: {field, op, value}."
        ),
    )
    query: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "For TABLE resources, optional read-only SQL query for RLS-disabled "
            "tables. Use filters for normal table-scoped record display."
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
        if request.interactive:
            return "interactive is only valid for WIDGET resources."

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


class AskUserOption(BaseModel):
    label: str = Field(
        description="The choice shown to the user — concise (1-5 words)."
    )
    description: str = Field(
        default="",
        description="Optional one-line explanation of what this option means.",
    )
    recommended: bool = Field(
        default=False,
        description="Set on the option you recommend; the client highlights it.",
    )


class AskUserQuestion(BaseModel):
    question: str = Field(description="The full question to ask the user.")
    header: str = Field(
        description=(
            "Very short label for this question (a few words), shown as a chip and "
            "used as the key for this question's answer."
        )
    )
    options: list[AskUserOption] = Field(
        description="2-4 distinct choices for the user to pick from."
    )
    multi_select: bool = Field(
        default=False,
        description="Allow the user to select multiple options instead of just one.",
    )


class AskUserRequest(BaseModel):
    questions: list[AskUserQuestion] = Field(
        description=(
            "One or more questions to ask the user at once. Each has a short "
            "header, the question text, and 2-4 options. The user can always also "
            "choose 'Other' and type a custom answer, so do NOT add an 'Other' "
            "option yourself."
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
