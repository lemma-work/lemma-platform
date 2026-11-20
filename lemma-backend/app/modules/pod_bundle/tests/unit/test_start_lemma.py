"""Starting a Lemma: what the one call takes, and what it writes first."""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.modules.pod.contracts.outcome import OutcomeInput, PodOutcome
from app.modules.pod_bundle.api.start_schemas import StartLemmaRequest
from app.modules.pod_bundle.application.start_use_cases import (
    TAUGHT_NOTE,
    setup_request,
    template_index_line,
    template_note,
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


def test_a_lemma_from_its_own_words_is_given_no_note_it_was_never_taught():
    assert template_note(None) is None


def test_a_templates_rules_are_its_first_memory_note():
    card = shipped_card("support")
    assert card is not None
    note = template_note(card)
    assert note is not None
    for line in [*card.plan.how, *card.plan.never]:
        assert f"- {line}" in note
    assert "## Never" in note
    assert "---" not in note, "no front matter: what it owns is on the pod"
    assert f"({TAUGHT_NOTE})" in template_index_line(card), (
        "the index every run loads points at the note"
    )


def test_a_template_with_no_rules_writes_no_note():
    card = shipped_card("support")
    assert card is not None
    bare = card.model_copy(
        update={"plan": card.plan.model_copy(update={"how": [], "never": []})}
    )
    assert template_note(bare) is None


def test_the_first_message_asks_the_agent_to_set_itself_up():
    own = PodOutcome.model_validate(OWN_WORDS)
    assert "set_what_you_own" not in setup_request(own, None)
    assert "own this" not in setup_request(own, None), (
        "a Lemma is not asked to own a result"
    )
    card = shipped_card("launch")
    assert card is not None
    started = PodOutcome.model_validate(
        card.outcome.for_due(date(2026, 11, 18)).model_dump()
    )
    said = setup_request(started, card)
    assert "Launch template" in said and "November 18" in said
    assert "set-up skill" in said
    assert "lemma-setup" in setup_request(own, None)
    assert "plan.md" not in said and "plan.md" not in setup_request(own, None)
    assert "memory" in said, "what it is taught is kept in its memory"
    # The sentence keeps its own full stop; the quote does not add a second.
    assert '.".' not in said and '."' in said


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
