"""A role on the hiring shelf, as the template that makes it describes itself.

The shelf used to be a list in the frontend, written beside the templates and
kept in step with them by hand. A card is now read from the template: the prose
-- the job in one line, what arrives, the first things to say, the standing
work on offer -- from the ``role`` block of its ``pod.json``, and the rest from
what the template actually carries. Its skills are the ``SKILL.md`` files it
ships, its tables are the tables it creates, and what it is judged on is the
measures its seeded scorecard turns on. A card therefore cannot promise a skill
or a measure the hire will not get, and adding a role is adding a template.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TemplateNeed:
    """A place a first win reads from, named so the card can offer to connect
    it rather than pretend it already is."""

    connector: str
    label: str


@dataclass(frozen=True, slots=True)
class TemplateWin:
    """A first thing to say to the new teammate, in the person's words."""

    say: str
    needs: TemplateNeed | None = None


@dataclass(frozen=True, slots=True)
class TemplateOffer:
    """Standing work the role offers to take on. Turned on by a person after
    the hire, never by the hire itself."""

    title: str
    detail: str
    cron: str
    instruction: str


@dataclass(frozen=True, slots=True)
class TemplateSkill:
    name: str
    description: str


@dataclass(frozen=True, slots=True)
class TemplateTable:
    name: str
    description: str


@dataclass(frozen=True, slots=True)
class TemplateCard:
    template: str
    name: str
    role: str
    about: str
    seed: str
    brings: tuple[str, ...]
    wins: tuple[TemplateWin, ...]
    offers: tuple[TemplateOffer, ...]
    judged_on: tuple[str, ...]
    skills: tuple[TemplateSkill, ...]
    tables: tuple[TemplateTable, ...]
    position: int
