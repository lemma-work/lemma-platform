"""An approval card says what "allow for this conversation" allows.

A session approval covers the permission, not the one call, and the card has
to say so in words a person reads before they tap it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.core.authorization.approval_wording import (
    _SCOPE,
    grant_note,
    permission_scope,
    session_scope_sentence,
)

pytestmark = pytest.mark.unit

_FRONTEND_TABLE = (
    Path(__file__).resolve().parents[6]
    / "lemma-frontend"
    / "src"
    / "thread"
    / "approval-scope.ts"
)


def test_a_record_delete_says_it_covers_every_table():
    assert (
        session_scope_sentence(["datastore.record.delete"])
        == "Delete records in any table for the rest of this conversation"
    )


def test_every_permission_the_card_lends_is_named():
    assert session_scope_sentence(
        ["datastore.table.read", "datastore.record.delete", "datastore.table.read"]
    ) == (
        "Delete records in any table and read any table "
        "for the rest of this conversation"
    )


def test_with_no_permission_it_covers_the_exact_command_only():
    assert session_scope_sentence([]) == (
        "Run this exact command again without asking, for the rest of this conversation"
    )


def test_a_permission_without_a_phrase_reads_as_its_description():
    assert permission_scope("web_login.use") == (
        "sign in to a saved site with a stored session"
    )


def test_a_grant_card_says_the_grant_stays():
    assert grant_note("gmail") == (
        "Approving gives this agent the gmail connector from now on, in every "
        "conversation, until someone removes it."
    )


@pytest.mark.skipif(not _FRONTEND_TABLE.exists(), reason="no frontend checkout")
def test_the_web_card_uses_the_same_words():
    """The web card renders from its own copy of the table; it must not drift
    from what Slack, Teams and Telegram are told."""
    source = _FRONTEND_TABLE.read_text()
    table = dict(re.findall(r'^\s*"([a-z_.]+)": "([^"]+)",$', source, re.MULTILINE))
    assert table == _SCOPE
