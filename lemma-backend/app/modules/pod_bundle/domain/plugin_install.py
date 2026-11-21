"""A plugin install's rules, with no I/O: the page's defaults, and what it says.

A plugin install applies each bundle as its install page would with nobody
choosing anything (:func:`page_defaults`): a required name it can derive is
derived from the Lemma's name, a connector with no installation gets Lemma's
own app where the importer may make one, and nothing else is chosen. Where the
page would put a question in front of the person -- something already in the
Lemma would change, a name it cannot derive, a connector it cannot map -- the
install stops before applying and keeps the question to say.

The web app's sign-up did the same from the browser; these are its rules
(``lemma-frontend/src/arrival/install-plan.ts``), moved to the server so they
hold when nobody is watching. One difference on purpose: an optional variable
with no default -- only a deprecated ``account`` variable is one -- is left
empty, as its own description asks, rather than given the Lemma's name.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from app.modules.pod_bundle.domain.connector_plan import (
    ConnectorChoice,
    ConnectorPlan,
    ConnectorPlanStatus,
)
from app.modules.pod_bundle.domain.state import (
    ImportPlan,
    ImportState,
    ImportStatus,
    StepKind,
    VariableSpec,
)

#: How many bundles one install may name: the daily import cap's default, so
#: one request cannot be most of a day's imports twice over.
MAX_BUNDLES_PER_INSTALL = 10


@dataclass(frozen=True, slots=True)
class PageDefaults:
    """What the install page would apply with, had nobody changed anything."""

    variables: dict[str, str]
    connectors: dict[str, ConnectorChoice]


def slug_of(name: str) -> str:
    """A name as a machine reads it: ``Smart Inbox`` -> ``smart-inbox``."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _value(variable: VariableSpec, name: str) -> str:
    if variable.default:
        return variable.default
    if variable.kind == "free" and variable.required:
        return slug_of(name)
    return ""


def _creates_installation(connector: ConnectorPlan) -> bool:
    return (
        connector.auth_config_id is None
        and connector.can_create_installation
        and connector.status
        not in (ConnectorPlanStatus.EXISTS, ConnectorPlanStatus.CONFLICT)
    )


def _connector_question(connector: ConnectorPlan) -> str | None:
    if connector.status is ConnectorPlanStatus.CONFLICT:
        return connector.message or "Its name is taken in this Lemma."
    if (
        connector.status is ConnectorPlanStatus.EXISTS
        or connector.auth_config_id is not None
        or _creates_installation(connector)
        or connector.status is ConnectorPlanStatus.SKIPPED
    ):
        return None
    return (
        connector.message
        or f"Your organization has no {connector.connector} installation."
    )


def page_defaults(plan: ImportPlan, name: str) -> PageDefaults | str:
    """The page's defaults for ``plan``, or the question that stops it.

    ``name`` is the Lemma's: what the page derives a required name (an app's
    slug) from. A ``pod_member`` variable is left empty, and the apply fills
    it with the person installing.
    """
    if plan.has_destructive_steps:
        return "It would change something already in this Lemma."
    for variable in plan.variables:
        if variable.required and not _value(variable, name):
            return f"It needs a value for {variable.name}."
    connectors: dict[str, ConnectorChoice] = {}
    for connector in plan.connectors:
        question = _connector_question(connector)
        if question is not None:
            return question
        if _creates_installation(connector):
            connectors[connector.slug] = ConnectorChoice(create_installation=True)
    return PageDefaults(
        variables={v.name: _value(v, name) for v in plan.variables},
        connectors=connectors,
    )


class PluginInstallState(str, Enum):
    """Where one bundle of an install is, as the person would say it."""

    #: Queued behind the bundles before it.
    WAITING = "WAITING"
    #: Being fetched, planned or applied.
    INSTALLING = "INSTALLING"
    #: Every step landed.
    INSTALLED = "INSTALLED"
    #: Stopped before anything was applied: its install page has a question.
    NEEDS_ANSWER = "NEEDS_ANSWER"
    #: Stopped. Retrying the install continues it from the step that failed.
    FAILED = "FAILED"
    #: Somebody cancelled it.
    CANCELLED = "CANCELLED"


_SETTLED = frozenset(
    {
        PluginInstallState.INSTALLED,
        PluginInstallState.NEEDS_ANSWER,
        PluginInstallState.FAILED,
        PluginInstallState.CANCELLED,
    }
)


def outcome_of(state: ImportState) -> PluginInstallState:
    if state.install is not None and state.install.asks is not None:
        return PluginInstallState.NEEDS_ANSWER
    match state.status:
        case ImportStatus.QUEUED:
            return PluginInstallState.WAITING
        case ImportStatus.COMPLETED:
            return PluginInstallState.INSTALLED
        case ImportStatus.FAILED:
            return PluginInstallState.FAILED
        case ImportStatus.CANCELLED | ImportStatus.PARTIALLY_CANCELLED:
            return PluginInstallState.CANCELLED
        case _:
            return PluginInstallState.INSTALLING


def settled(states: Sequence[ImportState]) -> bool:
    """Whether every bundle has stopped moving: nothing is waiting or running."""
    return all(outcome_of(state) in _SETTLED for state in states)


def bundle_name(state: ImportState) -> str:
    """What the bundle calls itself, or the last part of its folder."""
    if state.plan is not None and state.plan.bundle_name:
        return state.plan.bundle_name
    folder = (state.source.path or state.source.repo_url or "").rstrip("/")
    return folder.rsplit("/", 1)[-1].replace("-", " ").replace("_", " ").title()


def installed_app(state: ImportState) -> str | None:
    """The app the bundle put in the Lemma: its first, the one it is built
    around. ``None`` until it is installed, and for a bundle with no app."""
    if state.status is not ImportStatus.COMPLETED or state.plan is None:
        return None
    return next(
        (step.name for step in state.plan.steps if step.kind is StepKind.APP), None
    )


def _listed(names: Sequence[str]) -> str:
    if len(names) < 2:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def settled_note(states: Sequence[ImportState]) -> str:
    """What the Lemma tells the person once its install has settled.

    One note for the whole install, not one per bundle: they asked once and
    left, and come back to one answer. Nothing here is a plugin -- to the
    person these are apps.
    """
    installed = [
        bundle_name(s) for s in states if outcome_of(s) is PluginInstallState.INSTALLED
    ]
    lines: list[str] = []
    if installed:
        verb = "is" if len(installed) == 1 else "are"
        lines.append(f"{_listed(installed)} {verb} ready.")
    for state in states:
        name, outcome = bundle_name(state), outcome_of(state)
        if outcome is PluginInstallState.NEEDS_ANSWER and state.install is not None:
            lines.append(
                f"{name} didn't go in. {state.install.asks} Finish it on its own page."
            )
        elif outcome is PluginInstallState.FAILED:
            lines.append(f"{name} didn't finish. {state.error or ''}".rstrip())
    return "\n".join(lines)


__all__ = [
    "MAX_BUNDLES_PER_INSTALL",
    "PluginInstallState",
    "PageDefaults",
    "bundle_name",
    "installed_app",
    "outcome_of",
    "page_defaults",
    "settled",
    "settled_note",
    "slug_of",
]
