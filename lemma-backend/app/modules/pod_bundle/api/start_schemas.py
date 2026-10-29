"""Request and response models for starting a Lemma (``POST /pods/start``).

Apart from ``schemas.py`` beside it, which is the bundle import, export and
publish API.
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.modules.pod.contracts.outcome import OutcomeInput, PodOutcome
from app.modules.pod_bundle.domain.state import TEMPLATE_NAME_PATTERN


class StartLemmaRequest(BaseModel):
    """Body for starting a Lemma: from a template, or from its own words."""

    organization_id: UUID
    template: str | None = Field(
        default=None,
        pattern=TEMPLATE_NAME_PATTERN,
        description=(
            "A template from GET /pods/bundle/templates. It decides what the "
            "Lemma owns and brings the tables, skill and schedules that count it."
        ),
    )
    outcome: OutcomeInput | None = Field(
        default=None,
        description=(
            "What it owns, for a Lemma started without a template. Without a "
            "measure: nothing it could count exists yet, so its agent sets one "
            "once it has built what it counts."
        ),
    )
    due: date | None = Field(
        default=None,
        description="For a template that is a finish line: the date it is due by.",
    )
    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description=(
            "Its short name: the template's when omitted, and required without "
            "one. A name already taken in the organization gets a number after it."
        ),
    )
    description: str | None = Field(
        default=None,
        description="What it is for. A template's own when omitted.",
    )

    @model_validator(mode="after")
    def _one_way_to_start(self) -> "StartLemmaRequest":
        if (self.template is None) == (self.outcome is None):
            raise ValueError("Start from a template or from what it owns, not both.")
        if self.outcome is None:
            return self
        if self.name is None:
            raise ValueError("A Lemma started from its own words needs a name.")
        if self.due is not None:
            raise ValueError(
                "due is a template's; a finish line of your own has it in outcome."
            )
        if self.outcome.measure is not None:
            raise ValueError(
                "Nothing it could count exists yet: set the measure later with "
                "PUT /pods/{pod_id}/outcome, or let its agent set it."
            )
        return self


class StartedPodResponse(BaseModel):
    id: UUID
    organization_id: UUID
    name: str = Field(
        description="The name it got, numbered when the one asked for was taken."
    )
    description: str | None = None
    icon_url: str | None = None
    created_at: datetime | None = None
    outcome: PodOutcome | None = None


class StartLemmaResponse(BaseModel):
    pod: StartedPodResponse
    conversation_id: UUID = Field(
        description=(
            "The chat where its agent sets itself up, opened with the person's "
            "first message. From a template, its first turn starts once the "
            "template is applied."
        )
    )
    import_id: UUID | None = Field(
        default=None,
        description=(
            "The template's import, readable at GET /pods/{pod_id}/bundle/imports/"
            "{import_id} while it is applied; null without a template."
        ),
    )
