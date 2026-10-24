"""Every shipped template is a card a person can start a Lemma from."""

from __future__ import annotations

import json
from datetime import date

from app.modules.datastore.services.sql_introspection import analyze_query
from app.modules.pod.contracts.outcome import OutcomeKind
from app.modules.pod_bundle.infrastructure.outcome_templates import (
    DUE,
    shipped_card,
    shipped_cards,
)
from app.modules.pod_bundle.infrastructure.templates import (
    TEMPLATES_ROOT,
    shipped_template_names,
)


def _tables_brought(template: str) -> set[str]:
    tables = TEMPLATES_ROOT / template / "tables"
    return {
        json.loads((folder / f"{folder.name}.json").read_text())["name"]
        for folder in tables.iterdir()
        if folder.is_dir()
    }


def test_every_shipped_template_is_on_the_shelf_in_order():
    cards = shipped_cards()
    assert {card.id for card in cards} == set(shipped_template_names())
    assert [card.position for card in cards] == sorted(card.position for card in cards)


def test_bars_hold_every_week_and_finish_lines_wait_for_a_date():
    for card in shipped_cards():
        assert (DUE in card.sentence) == (card.kind is OutcomeKind.DATE), card.id
        assert 1 <= len(card.brings) <= 3, card.id
        assert card.face, card.id
        assert card.plan.how and card.plan.never, card.id


def test_every_measure_is_one_read_only_query_over_the_tables_it_brings():
    """Checked here because starting a Lemma keeps a template's measure without
    reading it: the tables it counts do not exist until the template is in."""
    for card in shipped_cards():
        assert card.outcome.measure, card.id
        assert card.outcome.target is not None, card.id
        counted = analyze_query(card.outcome.measure).tables
        assert counted and counted <= _tables_brought(card.id), card.id


def test_a_finish_line_takes_its_date_into_its_sentence():
    launch = shipped_card("launch")
    assert launch is not None
    outcome = launch.outcome.for_due(date(2026, 11, 18))
    assert outcome.sentence == "The launch ships on November 18."
    assert outcome.due == date(2026, 11, 18)
    support = shipped_card("support")
    assert support is not None and support.outcome.for_due(None).due is None
