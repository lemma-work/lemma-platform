"""One thing a person receives, and what became of each part of it.

The adapter port grew a verb per kind of content -- ``_render_choices``,
``_render_decision``, ``_render_voice``, ``_render_file``,
``_render_resource`` -- each answered by every platform, most of them with
a default that returns ``False``. Eighteen verbs across seven platforms is 126
cells, and a cell that was never written looks exactly like a platform that
declined: that is how ``acknowledge_interaction`` came to be implemented once,
and how three email adapters ended up with a working ``_render_resource``
nothing calls.

An envelope inverts it. Content is *data* with a text degradation defined once,
beside the part rather than per platform, and a platform's job is to render
whatever parts it can and fall back on the rest. "Does this platform support
choices" stops being a method that might not exist and becomes a branch inside
one method that always does.

The receipt is the other half. A part can land three ways -- natively, degraded
to text or a link, or not at all -- and the caller usually wants to know which
without having to ask each verb whether it returned ``True``.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from app.modules.agent_surfaces.domain.models import (
    SurfaceApprovalRenderPlan,
    SurfaceDisplayRenderPlan,
    SurfaceQuestionRenderPlan,
)

__all__ = [
    "DeliveryReceipt",
    "EnvelopeFile",
    "EnvelopeVoice",
    "PartDelivery",
    "SurfaceEnvelope",
]


class PartDelivery(StrEnum):
    """How one part of an envelope reached the person."""

    #: Rendered as the platform's own control -- buttons, a card, an attachment.
    NATIVE = "NATIVE"
    #: Delivered, but as text or a link because the platform cannot do better.
    #: Not a failure: it is the promise that nothing is dropped for lack of
    #: native support.
    DEGRADED = "DEGRADED"
    #: Did not reach the person at all.
    UNDELIVERED = "UNDELIVERED"


class EnvelopeFile(BaseModel):
    """A file already resolved to bytes, plus what to show if it cannot land.

    Loading stays with the caller that has the pod: an adapter renders, it does
    not go looking for content. ``fallback`` is the link card shown when the
    bytes are over the platform's cap or it cannot attach at all -- a plan
    rather than a bare URL, so the degradation is the same card the resource
    part would render, and it degrades once more to text on a platform with no
    cards. Without one the file degrades to its caption and name, which is worth
    more than silence but not much.
    """

    file_name: str
    content: bytes
    mime_type: str
    caption: str | None = None
    fallback: SurfaceDisplayRenderPlan | None = None
    # The pod path these bytes came from, when there was one. Provenance, and
    # the one thing a platform that attaches by *URL* rather than by bytes
    # needs -- Composio's mail actions download a signed link server-side, so
    # they cannot use `content` at all.
    source_path: str | None = None
    # A signed link to the same bytes, for a platform that attaches by URL
    # instead of by content. Resolved by the caller that has the pod, because
    # signing needs pod services an adapter has no way to reach.
    signed_url: str | None = None


class EnvelopeVoice(BaseModel):
    """Audio meant to arrive as a voice note where the platform has them.

    Degrades to the same bytes as an ordinary attachment -- a platform without
    voice notes still has an audio player -- and only then to ``fallback``.
    """

    file_name: str
    content: bytes
    mime_type: str
    caption: str | None = None
    fallback: SurfaceDisplayRenderPlan | None = None


class SurfaceEnvelope(BaseModel):
    """What one delivery puts in front of a person.

    Every field is optional and an envelope may carry several: the narration
    that led up to a question and the question itself are one thing the person
    receives, not two, and sending them as two is how the lead-in used to arrive
    after the prompt on a platform that reordered.
    """

    text: str | None = None
    resources: list[SurfaceDisplayRenderPlan] = Field(default_factory=list)
    files: list[EnvelopeFile] = Field(default_factory=list)
    voice: EnvelopeVoice | None = None
    choices: SurfaceQuestionRenderPlan | None = None
    decision: SurfaceApprovalRenderPlan | None = None

    def is_empty(self) -> bool:
        return not any(
            (
                (self.text or "").strip(),
                self.resources,
                self.files,
                self.voice,
                self.choices,
                self.decision,
            )
        )


class DeliveryReceipt(BaseModel):
    """What became of each part, keyed by the envelope field it came from."""

    parts: dict[str, PartDelivery] = Field(default_factory=dict)

    @property
    def delivered(self) -> bool:
        """Did anything at all reach the person?"""
        return any(
            outcome is not PartDelivery.UNDELIVERED for outcome in self.parts.values()
        )

    @property
    def undelivered(self) -> list[str]:
        """The parts that reached nobody, for a caller that must say so."""
        return sorted(
            name
            for name, outcome in self.parts.items()
            if outcome is PartDelivery.UNDELIVERED
        )

    @property
    def degraded(self) -> list[str]:
        """The parts the platform could not render natively."""
        return sorted(
            name
            for name, outcome in self.parts.items()
            if outcome is PartDelivery.DEGRADED
        )
