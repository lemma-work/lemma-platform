"""A schedule names a pod connector, never an account.

`schedules.account_id` pinned whichever account its author picked, so a
schedule kept listening through it after its owner left the pod, and an editor
could not take it over without borrowing somebody else's credentials. The
account is now the pod connector's decision, resolved on every use; the row
holds the connector's slug and nothing that points into `accounts`.

Asserted against the mapped model: the behaviour is the DDL's.
"""

from __future__ import annotations

from app.modules.schedule.infrastructure.models.schedule import Schedule


def test_no_column_points_into_accounts():
    assert "account_id" not in Schedule.__table__.columns
    assert not [
        fk
        for fk in Schedule.__table__.foreign_keys
        if fk.target_fullname.startswith("accounts.")
    ]


def test_the_pod_connector_is_held_by_slug_beside_the_pod():
    column = Schedule.__table__.columns["pod_connector_slug"]
    assert column.nullable
    assert not column.foreign_keys, (
        "no foreign key: a connector removed from the pod leaves a reference "
        "that fails typed, not a NULL that reads as listening through nothing"
    )
    index = next(
        index
        for index in Schedule.__table__.indexes
        if index.name == "ix_schedules_pod_connector"
    )
    assert [column.name for column in index.columns] == ["pod_id", "pod_connector_slug"]


def test_the_trigger_reference_still_nulls_rather_than_cascades():
    """Deleting a trigger from the catalog must not take run history with it."""
    by_target = {
        fk.target_fullname: fk.ondelete for fk in Schedule.__table__.foreign_keys
    }
    assert by_target["connector_triggers.id"] == "SET NULL"
