"""A reply whose send hit Lemma's own database being unreachable is retried.

The platform did not refuse it, so FAILED for good -- what a permanent refusal
earns -- would drop a reply nobody has seen.
"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError, OperationalError

from app.modules.agent_surfaces.services.delivery_sender import _failed_send


class _TooMany(Exception):
    sqlstate = "53300"


def test_a_send_cut_by_the_database_being_unavailable_is_transient() -> None:
    result = _failed_send(OperationalError("SELECT", {}, _TooMany("full")))
    assert result.delivered is False
    assert result.transient is True


def test_any_other_send_error_is_not() -> None:
    result = _failed_send(IntegrityError("INSERT", {}, Exception("dup")))
    assert result.transient is False
