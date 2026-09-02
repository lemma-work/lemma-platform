"""A connector account leaving must not take schedules with it.

`schedules.account_id` was `ON DELETE CASCADE`, and `accounts` cascades from
`auth_configs`, and `schedule_runs` cascades from `schedules`. One admin
removing a misconfigured install therefore deleted every member's account,
every webhook schedule bound to those accounts, and the whole run history of
each -- with no warning and no way back. Migration 0027 makes it `SET NULL`,
matching what `agent_surfaces.account_id` and `schedules.connector_trigger_id`
already do.

Note what is deliberately NOT asserted here: that a schedule left holding no
account stops firing. `account_id` is optional on a webhook schedule -- the
Composio path resolves the account from the payload's `connected_account_id`
at execution time -- so a null is an ordinary state, not an orphan, and a
matcher that skipped them would break every working Composio webhook. Losing
the account degrades such a schedule; it no longer destroys it.

The database half is asserted against the mapped model rather than a live
delete, because the behaviour lives in the DDL: a test that inserted rows and
deleted them would prove the same thing more slowly and only where a database
is running.
"""

from __future__ import annotations

from app.modules.schedule.infrastructure.models.schedule import Schedule


def test_the_account_foreign_key_nulls_rather_than_cascades():
    """The whole bug in one assertion. CASCADE here destroyed rows two tables
    away through `schedule_runs`."""
    # `target_fullname` rather than `fk.column`: resolving the column needs
    # every referenced table in the metadata, and `agents` lives in a module
    # this test has no reason to import.
    foreign_key = next(
        fk
        for fk in Schedule.__table__.foreign_keys
        if fk.target_fullname == "accounts.id"
    )
    assert foreign_key.ondelete == "SET NULL"


def test_it_matches_the_sibling_relationships_that_were_always_right():
    """`connector_trigger_id` on this same table has been SET NULL all along.
    Two references to a connector on one row behaving differently is what let
    this go unnoticed."""
    by_target = {
        fk.target_fullname: fk.ondelete for fk in Schedule.__table__.foreign_keys
    }
    assert by_target["accounts.id"] == "SET NULL"
    assert by_target["connector_triggers.id"] == "SET NULL"
