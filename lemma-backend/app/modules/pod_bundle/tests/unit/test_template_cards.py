"""Reading a role card off a template directory."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.modules.pod_bundle.infrastructure.template_cards import (
    read_template_card,
    template_cards,
)

_ROLE = {
    "line": "Does the job",
    "seed": "arch/job/1",
    "brings": ["A table"],
    "wins": [
        {"say": "Start here.", "needs": {"connector": "github", "label": "GitHub"}}
    ],
    "offers": [
        {
            "title": "Daily",
            "detail": "Each day",
            "cron": "0 9 * * 1-5",
            "instruction": "Do it.",
        }
    ],
}

_SCORECARD = (
    "id,key,measure,kind,counter,is_on,position\n"
    "1,second,Second of its own,outcome,work,true,2\n"
    "2,shared,Standing work runs on time,reliability,standing_work,true,3\n"
    "3,off,Turned off,outcome,work,false,4\n"
    "4,first,First of its own,outcome,work,true,1\n"
)


def _template(
    root: Path,
    name: str,
    *,
    role: object = _ROLE,
    title: str = "Job",
    position: int | None = None,
) -> Path:
    directory = root / name
    directory.mkdir(parents=True)
    manifest: dict[str, object] = {
        "name": title,
        "description": "About it.",
        "format_version": 3,
    }
    if role is not None:
        block = dict(role) if isinstance(role, dict) else role
        if position is not None and isinstance(block, dict):
            block["position"] = position
        manifest["role"] = block
    (directory / "pod.json").write_text(json.dumps(manifest))
    return directory


def test_card_reads_prose_from_the_role_block_and_the_rest_from_the_template(
    tmp_path: Path,
):
    directory = _template(tmp_path, "job")
    (directory / "tables" / "scorecard").mkdir(parents=True)
    (directory / "tables" / "scorecard" / "data.csv").write_text(_SCORECARD)
    (directory / "tables" / "tickets").mkdir(parents=True)
    (directory / "tables" / "tickets" / "tickets.json").write_text(
        json.dumps(
            {
                "name": "tickets",
                "config": {"description": "One per ticket."},
                "columns": [],
            }
        )
    )
    (directory / "files" / "skills" / "triage").mkdir(parents=True)
    (directory / "files" / "skills" / "triage" / "SKILL.md").write_text(
        "---\nname: triage\ndescription: Sort what came in.\n---\n\n# Triage\n"
    )

    card = read_template_card("job", directory)

    assert card is not None
    assert (card.name, card.role, card.about, card.seed) == (
        "Job",
        "Does the job",
        "About it.",
        "arch/job/1",
    )
    assert card.wins[0].needs is not None and card.wins[0].needs.connector == "github"
    assert card.offers[0].cron == "0 9 * * 1-5"
    # Its own measures that are on, in position order; the shared one is not the role's.
    assert card.judged_on == ("First of its own", "Second of its own")
    assert [(skill.name, skill.description) for skill in card.skills] == [
        ("triage", "Sort what came in.")
    ]
    # The scorecard is how it is judged, not a table of its work.
    assert [(table.name, table.description) for table in card.tables] == [
        ("tickets", "One per ticket.")
    ]


def test_a_template_without_a_role_block_is_not_on_the_shelf(tmp_path: Path):
    assert read_template_card("plain", _template(tmp_path, "plain", role=None)) is None


@pytest.mark.parametrize(
    "role",
    [
        {**_ROLE, "brings": ["one", "two", "three", "four"]},
        {**_ROLE, "wins": []},
        {**_ROLE, "offers": [{**_ROLE["offers"][0], "cron": "every morning"}]},
        {**_ROLE, "surprise": True},
    ],
)
def test_a_malformed_role_block_is_refused(tmp_path: Path, role: dict[str, object]):
    with pytest.raises(ValidationError):
        read_template_card("bad", _template(tmp_path, "bad", role=role))


def test_the_shelf_skips_a_bad_card_and_orders_the_rest(tmp_path: Path):
    _template(tmp_path, "later", title="Later", position=2)
    _template(tmp_path, "sooner", title="Sooner", position=1)
    _template(tmp_path, "broken", role={"line": "No seed"})
    _template(tmp_path, "unlisted", role=None)

    assert [card.template for card in template_cards(tmp_path)] == ["sooner", "later"]
