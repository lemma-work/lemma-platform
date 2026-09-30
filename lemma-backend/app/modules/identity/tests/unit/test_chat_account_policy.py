"""Email verification counts toward a chat account only where it is required."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from sqlalchemy.dialects import postgresql

from app.core.config import settings
from app.modules.identity.infrastructure.chat_account_policy import (
    chat_account_clause,
    is_chat_account,
)

pytestmark = pytest.mark.unit


@dataclass
class _Account:
    is_active: bool = True
    is_deleted: bool = False
    is_verified: bool = False


@pytest.mark.parametrize(
    ("account", "required", "expected"),
    [
        (_Account(is_verified=True), True, True),
        (_Account(is_verified=False), True, False),
        (_Account(is_verified=False), False, True),
        (_Account(is_active=False), False, False),
        (_Account(is_deleted=True), False, False),
        (None, False, False),
    ],
)
def test_is_chat_account(monkeypatch, account, required, expected):
    monkeypatch.setattr(settings, "auth_email_verification_required", required)
    assert is_chat_account(account) is expected


@pytest.mark.parametrize("required", [True, False])
def test_the_sql_predicate_names_is_verified_only_when_required(
    monkeypatch, required
):
    monkeypatch.setattr(settings, "auth_email_verification_required", required)
    sql = str(chat_account_clause().compile(dialect=postgresql.dialect()))
    assert "is_active" in sql and "is_deleted" in sql
    assert ("is_verified" in sql) is required
