from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SurfaceSenderProfile(BaseModel):
    external_user_id: str | None = None
    email: str | None = None
    phone: str | None = None
    display_name: str | None = None
    raw_profile: dict[str, Any] = Field(default_factory=dict)


class SurfaceMessageMetadata(BaseModel):
    surface_platform: str
    sender_display_name: str | None = None
    sender_email: str | None = None
    sender_phone: str | None = None
    event_metadata: dict[str, Any] = Field(default_factory=dict)

    def as_message_metadata(self) -> dict[str, Any]:
        return {
            "surface_platform": self.surface_platform,
            "sender_display_name": self.sender_display_name,
            "sender_email": self.sender_email,
            "sender_phone": self.sender_phone,
            **self.event_metadata,
        }


class SurfaceChannelInfo(BaseModel):
    """A channel/group the surface bot can be configured to respond in."""

    id: str
    name: str | None = None
    is_member: bool | None = None


class SurfaceContextMessage(BaseModel):
    """One recent message from the thread/channel, fetched fresh per run to give
    the agent continuity in a group (where each user has a separate conversation).

    Background context only — never an instruction to act on.
    """

    author: str | None = None
    text: str
    ts: str | None = None


class SurfaceDisplayAction(BaseModel):
    label: str
    url: str
    kind: str = "open"


class SurfaceDisplayRenderPlan(BaseModel):
    """Platform-neutral resource display plan for external surfaces."""

    resource_type: str
    title: str
    summary: str | None = None
    detail_lines: list[str] = Field(default_factory=list)
    actions: list[SurfaceDisplayAction] = Field(default_factory=list)
    tool_call_id: str | None = None
    request: dict[str, Any] = Field(default_factory=dict)

    @property
    def primary_action(self) -> SurfaceDisplayAction | None:
        return self.actions[0] if self.actions else None

    def to_plain_text(self) -> str:
        lines = [self.title]
        if self.summary:
            lines.append(self.summary)
        lines.extend(line for line in self.detail_lines if line)
        action = self.primary_action
        if action:
            lines.append(f"{action.label}: {action.url}")
        return "\n".join(lines)

    def to_caption(self) -> str:
        """Title + summary + details, WITHOUT the action URL.

        For surfaces where a card or button already carries the link (Teams
        adaptive card, Slack blocks): the caption is used as the accompanying/
        notification text so the raw URL is never dumped inline next to the card.
        """
        lines = [self.title]
        if self.summary:
            lines.append(self.summary)
        lines.extend(line for line in self.detail_lines if line)
        return "\n".join(lines)


# Suffix marking a native "Other (type your own)" free-text input whose answer,
# when filled, overrides the selected option for the question keyed by the prefix.
OTHER_ANSWER_SUFFIX = "__other"


class SurfaceQuestionOption(BaseModel):
    """One selectable answer option for an ``ask_user`` question."""

    label: str
    description: str = ""
    recommended: bool = False


class SurfaceQuestion(BaseModel):
    """One ``ask_user`` question rendered as native tappable choices.

    ``header`` doubles as the answer key, so a native submission's values come
    back keyed by header and map straight into ``AskUserResponse.answers``.
    """

    header: str
    question: str
    options: list[SurfaceQuestionOption]
    multi_select: bool = False


class SurfaceQuestionRenderPlan(BaseModel):
    """Platform-neutral plan for rendering ``ask_user`` questions in-chat.

    Built from an ``ask_user`` request. ``callback_id`` carries the conversation
    + tool_call id so the submission can be routed back to the waiting agent run.
    ``allow_other`` reflects ask_user's always-available free-text "Other".
    """

    title: str
    questions: list[SurfaceQuestion]
    callback_id: str
    submit_label: str = "Submit"
    allow_other: bool = True
