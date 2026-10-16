"""A logged record change, as a DATASTORE trigger's delivery reads it.

What fires and what is skipped is ``deliver_page``'s (covered against real
Postgres in ``tests/e2e/test_trigger_delivery_e2e.py`` and the change-log e2e
tests); this covers the conversion in between: the row a condition sees, what
the write did, whose row it is, and whether it came from outside the pod.
"""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from app.modules.datastore.contracts.changes import Change, ChangeOp, ChangePosition
from app.modules.schedule.domain.value_objects import DatastoreOperation
from app.modules.schedule.services.datastore_event_handler import (
    OUTSIDE_ROW_NOTICE,
    trigger_change,
)
from app.modules.schedule.services.trigger_delivery import WriteIntent

AT = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def _change(op: ChangeOp, **overrides) -> Change:
    values = {
        "position": ChangePosition(41, 7),
        "table_id": uuid4(),
        "op": op,
        "record_id": "rec-1",
        "intent": "normal",
        "owner_user_id": None,
        "outside_actor": None,
        "event_id": None,
        "new_row": {"id": "rec-1", "status": "paid"},
        "old_values": None,
        "at": AT,
    }
    values.update(overrides)
    return Change(**values)


def test_an_insert_is_keyed_on_its_place_in_the_log_and_carries_its_row():
    change = trigger_change(_change(ChangeOp.INSERT), table_name="orders")
    assert change.source_event_id == "chg:41:7"
    assert change.operation is DatastoreOperation.INSERT
    assert dict(change.row) == {"id": "rec-1", "status": "paid"}
    assert change.metadata["table_name"] == "orders"
    assert change.metadata["event_occurred_at"] == AT.isoformat()
    assert change.previous is None and change.changed is None


def test_an_update_says_what_it_wrote_and_what_was_there_before():
    change = trigger_change(
        _change(ChangeOp.UPDATE, old_values={"status": "new", "total": 3}),
        table_name="orders",
    )
    assert change.changed == ["status", "total"]
    assert dict(change.previous or {}) == {"status": "new", "total": 3}


def test_a_delete_is_matched_against_the_row_it_removed():
    removed = {"id": "rec-1", "status": "void"}
    change = trigger_change(
        _change(ChangeOp.DELETE, new_row=None, old_values=removed), table_name="orders"
    )
    assert dict(change.row) == removed
    assert change.previous is None


def test_the_row_owner_and_the_write_intent_reach_the_trigger():
    owner = UUID(int=5)
    change = trigger_change(
        _change(ChangeOp.INSERT, owner_user_id=owner, intent="bulk"), table_name="t"
    )
    assert change.owner_user_id == owner
    assert change.intent is WriteIntent.BULK


def test_an_outside_row_is_marked_untrusted_in_so_many_words():
    change = trigger_change(
        _change(ChangeOp.INSERT, outside_actor="visitor:session-1"), table_name="t"
    )
    assert change.from_outside
    assert change.metadata["untrusted_row"] is True
    assert change.metadata["row_author"] == "visitor:session-1"
    assert change.metadata["row_notice"] == OUTSIDE_ROW_NOTICE


def test_a_member_row_is_not_marked():
    change = trigger_change(_change(ChangeOp.INSERT), table_name="t")
    assert not change.from_outside
    assert "untrusted_row" not in change.metadata


def test_a_change_converted_at_cutover_fires_under_the_old_events_id():
    event_id = uuid4()
    change = trigger_change(_change(ChangeOp.INSERT, event_id=event_id), table_name="t")
    assert change.source_event_id == str(event_id)
