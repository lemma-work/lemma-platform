"""Starting a Lemma: what the one call takes, and what it writes first."""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.modules.pod.contracts.outcome import OutcomeInput, PodOutcome
from app.modules.pod_bundle.api.start_schemas import StartLemmaRequest
from app.modules.pod_bundle.application.start_use_cases import (
    setup_request,
    starter_plan,
)
from app.modules.pod_bundle.domain.state import (
    BundleSource,
    BundleSourceKind,
    ImportPlan,
    ImportState,
    ImportStatus,
    VariableSpec,
)
from app.modules.pod_bundle.events.template_setup import applies_without_asking
from app.modules.pod_bundle.infrastructure.outcome_templates import shipped_card

ORG = uuid4()
OWN_WORDS = {
    "sentence": "Every invoice over 10000 gets a second approval.",
    "kind": "week",
}


def test_a_lemma_starts_from_a_template_or_from_its_own_words_not_both():
    StartLemmaRequest.model_validate({"organization_id": ORG, "template": "support"})
    StartLemmaRequest.model_validate(
        {"organization_id": ORG, "outcome": OWN_WORDS, "name": "Approvals"}
    )
    with pytest.raises(ValidationError, match="not both"):
        StartLemmaRequest.model_validate(
            {"organization_id": ORG, "template": "support", "outcome": OWN_WORDS}
        )
    with pytest.raises(ValidationError, match="not both"):
        StartLemmaRequest.model_validate({"organization_id": ORG})


def test_its_own_words_need_a_name_and_no_measure_yet():
    with pytest.raises(ValidationError, match="needs a name"):
        StartLemmaRequest.model_validate({"organization_id": ORG, "outcome": OWN_WORDS})
    with pytest.raises(ValidationError, match="set the measure later"):
        StartLemmaRequest.model_validate(
            {
                "organization_id": ORG,
                "name": "Approvals",
                "outcome": {**OWN_WORDS, "measure": "SELECT count(*) FROM invoices"},
            }
        )


def test_a_template_name_cannot_reach_outside_the_templates():
    with pytest.raises(ValidationError):
        StartLemmaRequest.model_validate(
            {"organization_id": ORG, "template": "../../etc"}
        )


def _sections(plan: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current = None
    for line in plan.splitlines():
        if line.startswith("## "):
            current = line[3:]
            sections[current] = []
        elif line.startswith("- ") and current is not None:
            sections[current].append(line[2:])
    return sections


def test_a_first_plan_has_the_four_sections_the_workspace_reads():
    outcome = PodOutcome.model_validate(OWN_WORDS)
    sections = _sections(starter_plan(outcome, None))
    assert list(sections) == [
        "What must be true",
        "How we work",
        "Who holds what",
        "Never",
    ]
    assert sections["What must be true"] == [outcome.sentence]


def test_a_templates_plan_starts_with_its_own_lines():
    card = shipped_card("support")
    assert card is not None
    outcome = PodOutcome.model_validate(card.outcome.for_due(None).model_dump())
    sections = _sections(starter_plan(outcome, card))
    assert sections["How we work"] == card.plan.how
    assert sections["Never"] == card.plan.never
    assert "---" not in starter_plan(outcome, card), (
        "no front matter: that is on the pod"
    )


def test_the_first_message_asks_the_agent_to_set_itself_up():
    own = PodOutcome.model_validate(OWN_WORDS)
    assert "set_what_you_own" in setup_request(own, None)
    card = shipped_card("launch")
    assert card is not None
    started = PodOutcome.model_validate(
        card.outcome.for_due(date(2026, 11, 18)).model_dump()
    )
    said = setup_request(started, card)
    assert "Launch template" in said and "November 18" in said


def _planned(**plan: object) -> ImportState:
    return ImportState(
        import_id=uuid4(),
        pod_id=uuid4(),
        user_id=uuid4(),
        status=ImportStatus.AWAITING_CONFIRMATION,
        source=BundleSource(kind=BundleSourceKind.TEMPLATE, template="support"),
        plan=ImportPlan(format_version=3, **plan),
    )


def test_a_template_is_applied_unless_its_plan_needs_the_person():
    assert applies_without_asking(_planned())
    assert not applies_without_asking(
        _planned(variables=[VariableSpec(name="account", required=True)])
    )
    unplanned = _planned()
    unplanned.plan = None
    assert not applies_without_asking(unplanned)


def test_a_template_outcome_is_an_ordinary_outcome_once_dated():
    card = shipped_card("partners")
    assert card is not None
    outcome = card.outcome.for_due(date(2026, 12, 1))
    assert isinstance(outcome, OutcomeInput)
    assert outcome.sentence == "20 design partners signed by December 1."
