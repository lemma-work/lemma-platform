"""The Lemmas a person can start from: one card per shipped template.

A template is a bundle under ``pod_bundle/templates/<name>/`` whose ``pod.json``
also says what a Lemma made from it owns (``outcome``) and how to show it as a
card (``lemma``): its face, where its work happens, what it brings and the
first lines of its plan. The card is read from those files, never written
twice.

A finish line's sentence carries ``{due}``, filled with the date the person
picks; its outcome is checked here with a stand-in date, so a template whose
outcome could never be set is left off the shelf rather than failing later.
"""

from __future__ import annotations

import json
from datetime import date
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from app.core.log.log import get_logger
from app.modules.pod.contracts.outcome import OutcomeInput, OutcomeKind
from app.modules.pod_bundle.infrastructure.templates import (
    TEMPLATES_ROOT,
    shipped_template_names,
    template_root,
)

logger = get_logger(__name__)

DUE = "{due}"


class TemplateOutcome(BaseModel):
    """What a template's Lemma owns, before its date is known: a finish
    line's sentence still carries ``{due}``."""

    sentence: str
    kind: OutcomeKind
    target: float | None = None
    unit: str | None = None
    measure: str | None = Field(
        default=None,
        description="Counts rows of the tables the template brings.",
    )

    def for_due(self, due: date | None) -> OutcomeInput:
        """The outcome to set, with a finish line's date in its sentence."""
        sentence = self.sentence
        if self.kind is OutcomeKind.DATE and due is not None:
            sentence = sentence.replace(DUE, f"{due:%B} {due.day}")
        return OutcomeInput(
            sentence=sentence,
            kind=self.kind,
            target=self.target,
            unit=self.unit,
            due=due if self.kind is OutcomeKind.DATE else None,
            measure=self.measure,
        )


class TemplatePlan(BaseModel):
    """The first lines of the plan a Lemma made from this template starts with."""

    how: list[str] = Field(default_factory=list)
    never: list[str] = Field(default_factory=list)


class OutcomeTemplateCard(BaseModel):
    """One Lemma a person can start, as the shelf shows it."""

    id: str = Field(
        description="The template's name, passed as `template` to start one."
    )
    name: str = Field(description="The short name a Lemma made from it starts with.")
    description: str | None = None
    sentence: str = Field(description="What it owns. A finish line's carries {due}.")
    kind: OutcomeKind
    face: str | None = Field(
        default=None, description="Which character's face it wears."
    )
    place: str | None = Field(
        default=None, description="Where its work usually happens."
    )
    brings: list[str] = Field(default_factory=list)
    outcome: TemplateOutcome = Field(
        description="What it owns once started, with {due} filled in."
    )
    plan: TemplatePlan = Field(default_factory=TemplatePlan)
    position: int = 100


def read_card(name: str, *, root: Path = TEMPLATES_ROOT) -> OutcomeTemplateCard | None:
    folder = template_root(name, root=root)
    if folder is None:
        return None
    try:
        manifest = json.loads((folder / "pod.json").read_text())
        lemma = manifest.get("lemma") or {}
        outcome = TemplateOutcome.model_validate(manifest.get("outcome"))
        # A stand-in date, so the check covers a finish line's due date too.
        outcome.for_due(date(2099, 12, 1))
        return OutcomeTemplateCard(
            id=name,
            name=str(manifest.get("name") or name),
            description=manifest.get("description"),
            sentence=outcome.sentence,
            kind=outcome.kind,
            face=lemma.get("face"),
            place=lemma.get("place"),
            brings=[str(item) for item in lemma.get("brings") or []][:3],
            outcome=outcome,
            plan=TemplatePlan.model_validate(lemma.get("plan") or {}),
            position=int(lemma.get("position") or 100),
        )
    except OSError, ValueError, AttributeError, ValidationError:
        logger.warning(
            "pod_bundle.template_card.unreadable.degraded", template=name, exc_info=True
        )
        return None


@lru_cache(maxsize=1)
def shipped_cards() -> tuple[OutcomeTemplateCard, ...]:
    """Every card this server ships, in shelf order. Read once: the files
    ship with the server and do not change while it runs."""
    cards = [
        card for card in (read_card(name) for name in shipped_template_names()) if card
    ]
    return tuple(sorted(cards, key=lambda card: (card.position, card.name)))


def shipped_card(name: str) -> OutcomeTemplateCard | None:
    """The card of the template called ``name``, or ``None``."""
    return next((card for card in shipped_cards() if card.id == name), None)
