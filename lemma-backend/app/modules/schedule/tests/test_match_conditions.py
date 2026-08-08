"""Semantics of deterministic DATASTORE match conditions.

The distinctions these tests pin down are the ones the feature exists for:
"is approved" is not "became approved", and "was written" is not "changed".
"""

import pytest

from app.modules.schedule.domain.match_conditions import (
    ColumnCondition,
    evaluate_match_conditions,
    parse_match_conditions,
)
from app.modules.schedule.domain.value_objects import DatastoreOperation


INSERT = DatastoreOperation.INSERT
UPDATE = DatastoreOperation.UPDATE
DELETE = DatastoreOperation.DELETE


def match(when, *, operation, payload, changed=None, previous=None) -> bool:
    return evaluate_match_conditions(
        parse_match_conditions(when),
        operation=operation,
        payload=payload,
        changed=changed,
        previous=previous,
    )


def test_no_conditions_matches_everything():
    assert (
        evaluate_match_conditions(
            None, operation=INSERT, payload={"status": "anything"}
        )
        is True
    )


def test_scalar_shorthand_is_equality():
    assert parse_match_conditions({"status": "approved"}) == {
        "status": ColumnCondition(equals="approved")
    }


def test_equality_reads_the_row_the_write_left_behind():
    assert match({"status": "approved"}, operation=INSERT, payload={"status": "approved"})
    assert not match(
        {"status": "approved"}, operation=INSERT, payload={"status": "pending"}
    )


def test_conditions_on_several_columns_all_have_to_hold():
    payload = {"status": "approved", "priority": "high"}
    assert match({"status": "approved", "priority": "high"}, operation=INSERT, payload=payload)
    assert not match(
        {"status": "approved", "priority": "low"}, operation=INSERT, payload=payload
    )


def test_membership_operators():
    assert match({"status": {"in": ["approved", "done"]}}, operation=INSERT, payload={"status": "done"})
    assert not match(
        {"status": {"not_in": ["approved", "done"]}},
        operation=INSERT,
        payload={"status": "done"},
    )


def test_became_is_not_the_same_as_is():
    """The distinction the whole feature turns on."""
    # pending -> approved is a transition.
    assert match(
        {"status": {"to": "approved"}},
        operation=UPDATE,
        payload={"status": "approved"},
        changed=["status"],
        previous={"status": "pending"},
    )
    # approved -> approved is a write, but not a transition.
    assert not match(
        {"status": {"to": "approved"}},
        operation=UPDATE,
        payload={"status": "approved"},
        changed=["status"],
        previous={"status": "approved"},
    )
    # An update that touched some other column leaves status alone, however
    # approved the row already is.
    assert not match(
        {"status": {"to": "approved"}},
        operation=UPDATE,
        payload={"status": "approved", "priority": "high"},
        changed=["priority"],
        previous={"priority": "low"},
    )


def test_a_row_created_approved_became_approved():
    """Otherwise every author needs a second trigger for the insert case."""
    assert match(
        {"status": {"to": "approved"}},
        operation=INSERT,
        payload={"status": "approved"},
    )
    assert not match(
        {"status": {"to": "approved"}},
        operation=INSERT,
        payload={"status": "pending"},
    )


def test_changed_asks_whether_the_value_moved():
    assert match(
        {"status": {"changed": True}},
        operation=UPDATE,
        payload={"status": "approved"},
        changed=["status"],
        previous={"status": "pending"},
    )
    # Written, but to the value it already held.
    assert not match(
        {"status": {"changed": True}},
        operation=UPDATE,
        payload={"status": "approved"},
        changed=["status"],
        previous={"status": "approved"},
    )


def test_written_asks_whether_anyone_touched_it():
    """`written` is the one thing the old submitted-keys payload could express."""
    assert match(
        {"notes": {"written": True}},
        operation=UPDATE,
        payload={"notes": "same"},
        changed=["notes"],
        previous={"notes": "same"},
    )
    assert not match(
        {"notes": {"written": True}},
        operation=UPDATE,
        payload={"notes": "same"},
        changed=["status"],
        previous={"status": "pending"},
    )


def test_from_matches_the_prior_value():
    assert match(
        {"status": {"from": "pending", "to": "approved"}},
        operation=UPDATE,
        payload={"status": "approved"},
        changed=["status"],
        previous={"status": "pending"},
    )
    assert not match(
        {"status": {"from": "draft", "to": "approved"}},
        operation=UPDATE,
        payload={"status": "approved"},
        changed=["status"],
        previous={"status": "pending"},
    )


def test_delete_matches_on_the_removed_row_but_never_on_a_transition():
    assert match({"status": "approved"}, operation=DELETE, payload={"status": "approved"})
    for undecidable in ({"to": "approved"}, {"from": "pending"}, {"changed": True}):
        assert not match({"status": undecidable}, operation=DELETE, payload={"status": "approved"})


def test_insert_cannot_satisfy_prior_image_operators():
    for undecidable in ({"from": "pending"}, {"changed": True}, {"written": True}):
        assert not match(
            {"status": undecidable}, operation=INSERT, payload={"status": "approved"}
        )


def test_negative_change_operators_hold_when_nothing_moved():
    assert match(
        {"status": {"changed": False}},
        operation=UPDATE,
        payload={"status": "approved", "priority": "high"},
        changed=["priority"],
        previous={"priority": "low"},
    )


def test_null_is_a_value_you_can_match_on():
    """`None` marks 'operator not supplied' in most schemas; here it must not."""
    assert match({"owner": {"equals": None}}, operation=INSERT, payload={"owner": None})
    assert not match(
        {"owner": {"equals": None}}, operation=INSERT, payload={"owner": "someone"}
    )


def test_a_column_missing_from_the_payload_cannot_satisfy_a_value_test():
    assert not match({"status": {"equals": None}}, operation=INSERT, payload={})


def test_unknown_operator_is_rejected_rather_than_ignored():
    """A typo that parsed would make the condition vacuous and fire on everything."""
    with pytest.raises(ValueError):
        parse_match_conditions({"status": {"equal": "approved"}})


def test_empty_condition_is_rejected():
    with pytest.raises(ValueError):
        parse_match_conditions({"status": {}})
    with pytest.raises(ValueError):
        parse_match_conditions({})
