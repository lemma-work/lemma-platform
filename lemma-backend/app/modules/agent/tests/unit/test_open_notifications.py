"""What the agent handling somebody's reply is told is outstanding.

The interesting case is a workflow form. The recipient answers in prose — over
email that is the whole interaction — and something has to turn "yes, 40 units,
PO-8812" into validated field values. That something is the agent already in
the conversation, which can only do it if it can see what the form asks for.
"""

from __future__ import annotations

from app.modules.agent.capabilities.open_notifications import (
    render_form_fields,
    render_open_notifications,
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "quantity": {"type": "integer", "title": "How many"},
        "po_number": {"type": "string"},
        "urgency": {"type": "string", "enum": ["low", "high"]},
    },
    "required": ["quantity"],
}


def test_nothing_open_costs_nothing():
    assert render_open_notifications([]) == ""


def test_a_form_assignment_names_the_fields_it_wants():
    """The gap this closes: the schema was stored and never shown.

    Being told a form is owed, and given only a run id and a node id, leaves the
    agent collecting "structured values" whose shape it cannot see — so it
    guesses field names, and the submission is rejected.
    """
    rendered = render_open_notifications(
        [
            {
                "notification_id": "n1",
                "title": "Purchase approval",
                "body": "How many units?",
                "responds_through_action": True,
                "action": {"run_id": "r1", "node_id": "collect", "schema": _SCHEMA},
            }
        ]
    )

    assert "`quantity` (integer; required)" in rendered
    assert "`po_number` (string)" in rendered
    # Enums matter most: they are the values a person will paraphrase.
    assert "one of `low`, `high`" in rendered
    # And it must point at the tool, not a free-text response.
    assert "submit_workflow_form" in rendered


def test_a_plain_ask_is_not_dressed_up_as_a_form():
    rendered = render_open_notifications(
        [
            {
                "notification_id": "n1",
                "title": "Standup",
                "body": "What did you ship?",
                "background_instruction": "Record it as the response summary.",
                "responds_through_action": False,
            }
        ]
    )

    assert "submit_workflow_form" not in rendered
    assert "Fields to collect" not in rendered
    assert "Record it as the response summary." in rendered
    assert "respond_to_notification" in rendered


def test_a_missing_or_malformed_schema_degrades_to_no_field_list():
    """A form with no usable schema is still answerable; it just says less.

    Raising here would take down the instructions for every *other* open ask in
    the same conversation.
    """
    assert render_form_fields(None) == []
    assert render_form_fields({"type": "object"}) == []
    assert render_form_fields("not a schema") == []
    assert render_form_fields({"properties": {}}) == []
