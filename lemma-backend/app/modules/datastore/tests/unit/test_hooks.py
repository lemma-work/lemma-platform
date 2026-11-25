"""Hooks: the secret, what a body becomes, how fast one is taken, and in what order
a delivery is refused."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.sql import Select

from app.modules.datastore.config_hooks import hook_settings
from app.modules.datastore.domain.datastore_entities import ColumnSchema
from app.modules.datastore.domain.hooks import (
    HOOK_COLUMN,
    PAYLOAD_COLUMN,
    RECEIVED_AT_COLUMN,
    HookDeliveryRefused,
    HookFields,
    content_type_problem,
    fields_problem,
    hook_row,
    name_problem,
    new_secret,
    parse_body,
    path_problem,
    secret_hash,
    secret_matches,
    value_at,
)
from app.modules.datastore.services.hook_limits import HookAllowance, HookLimiter
from app.modules.datastore.services.hooks import admit_delivery

RECEIVED = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def _column(name: str, type: str = "TEXT", **extra) -> ColumnSchema:
    return ColumnSchema(name=name, type=type, **extra)


INBOX = [
    _column("id", "UUID"),
    _column(PAYLOAD_COLUMN, "JSON", required=True),
    _column(RECEIVED_AT_COLUMN, "DATETIME"),
    _column(HOOK_COLUMN),
]


# --------------------------------------------------------------- the secret


def test_a_secret_is_kept_only_as_its_hash_and_matches_only_itself():
    secret = new_secret()
    stored = secret_hash(secret)

    assert secret not in stored
    assert secret.startswith("lmh_") and len(secret) > 40
    assert secret_matches(secret, stored)
    assert not secret_matches(secret + "x", stored)
    assert not secret_matches(new_secret(), stored)


@pytest.mark.parametrize(
    ("name", "ok"),
    [("typeform", True), ("tally-2", True), ("a_b", True), ("", False)]
    + [("Upper", False), ("-lead", False), ("x" * 65, False), ("a b", False)],
)
def test_a_name_is_lowercase_and_url_safe(name, ok):
    assert (name_problem(name) is None) is ok


# -------------------------------------------------------------- paths


def test_a_path_reads_objects_and_lists_and_dollar_is_the_whole_body():
    body = {"a": {"b": [{"c": 1}, {"c": 2}]}, "0": "key", "n": None}

    assert value_at(body, "a.b.1.c") == 2
    assert value_at(body, "0") == "key"
    assert value_at(body, "$") is body
    assert value_at(body, "a.b.5.c") is None
    assert value_at(body, "a.missing.c") is None
    assert value_at(body, "n.deeper") is None
    assert value_at(body, "a.b.x") is None


@pytest.mark.parametrize("path", ["", "a..b", ".a", "a.", "x" * 201])
def test_a_malformed_path_is_refused(path):
    assert path_problem(path) is not None


# ------------------------------------------------------------ what it may map


def test_with_no_fields_the_table_needs_a_json_payload_column():
    assert fields_problem(INBOX, "id", HookFields(None)) is None
    text_payload = [_column("id", "UUID"), _column(PAYLOAD_COLUMN)]
    assert "JSON column named 'payload'" in fields_problem(
        text_payload, "id", HookFields(None)
    )


@pytest.mark.parametrize(
    ("column", "why"),
    [
        (_column("owner", "USER"), "names a member"),
        (_column("cv", "FILE_PATH"), "names a file"),
        (_column("created_at", "DATETIME"), "automatically"),
        (_column("contact_id", "UUID"), "automatically"),
    ],
)
def test_a_hook_never_writes_what_a_stranger_must_not_choose(column, why):
    columns = [_column("id", "UUID"), column]

    problem = fields_problem(columns, "id", HookFields({column.name: "x"}))

    assert problem is not None and why in problem


def test_a_mapping_must_fill_every_required_column_or_the_hook_is_refused():
    columns = [
        _column("id", "UUID"),
        _column("email", required=True),
        _column("note", required=True, default="none"),
        _column(RECEIVED_AT_COLUMN, "DATETIME", required=True),
    ]

    assert "email" in fields_problem(columns, "id", HookFields({"note": "n"}))
    # received_at is stamped, and note has a default: email is all it needs.
    assert fields_problem(columns, "id", HookFields({"email": "e"})) is None
    assert "no such column" in fields_problem(
        columns, "id", HookFields({"email": "e", "nope": "x"})
    )
    assert "at least one" in fields_problem(columns, "id", HookFields({}))


# ------------------------------------------------------------- the body


@pytest.mark.parametrize(
    "content_type",
    [
        None,
        "application/json",
        "application/json; charset=utf-8",
        "application/vnd.x+json",
    ],
)
def test_json_or_no_content_type_is_taken(content_type):
    assert content_type_problem(content_type) is None


@pytest.mark.parametrize(
    "content_type", ["application/x-www-form-urlencoded", "text/plain"]
)
def test_anything_else_is_refused_with_how_to_send_json(content_type):
    problem = content_type_problem(content_type)

    assert problem is not None
    assert "application/json" in problem and "Zapier" in problem


@pytest.mark.parametrize(
    ("raw", "status"), [(b"{", 400), (b"\xff", 400), (b"[1]", 422), (b'"x"', 422)]
)
def test_a_body_that_is_not_a_json_object_is_refused(raw, status):
    with pytest.raises(HookDeliveryRefused) as refused:
        parse_body(raw)

    assert refused.value.status_code == status


def test_with_no_fields_the_whole_body_is_the_payload_and_the_row_is_stamped():
    body = {"email": "ada@example.com"}

    row = hook_row(
        INBOX, HookFields(None), body, hook_name="typeform", received_at=RECEIVED
    )

    assert row == {
        PAYLOAD_COLUMN: body,
        RECEIVED_AT_COLUMN: RECEIVED.isoformat(),
        HOOK_COLUMN: "typeform",
    }


def test_mapped_values_fit_their_column_and_a_missing_path_is_left_out():
    columns = [
        _column("id", "UUID"),
        _column("email"),
        _column("answers"),
        _column("agreed"),
        _column("score", "INTEGER"),
        _column("raw", "JSON"),
        _column("missing"),
        _column(HOOK_COLUMN, "INTEGER"),  # not text: not stamped
    ]
    body = {"e": "a@example.com", "list": [1, "two"], "ok": True, "s": 7}

    row = hook_row(
        columns,
        HookFields(
            {
                "email": "e",
                "answers": "list",
                "agreed": "ok",
                "score": "s",
                "raw": "$",
                "missing": "not.there",
            }
        ),
        body,
        hook_name="h",
        received_at=RECEIVED,
    )

    assert row == {
        "email": "a@example.com",
        "answers": '[1, "two"]',
        "agreed": "true",
        "score": 7,
        "raw": body,
    }


def test_a_stamp_column_a_field_maps_keeps_the_mapped_value():
    row = hook_row(
        INBOX,
        HookFields({PAYLOAD_COLUMN: "$", HOOK_COLUMN: "source"}),
        {"source": "website"},
        hook_name="h",
        received_at=RECEIVED,
    )

    assert row[HOOK_COLUMN] == "website"


# -------------------------------------------------------------- limits


class _Counters:
    """Redis as the limiter uses it: the increment script, and DECR."""

    def __init__(self) -> None:
        self.values: dict[str, int] = {}

    async def eval(self, _script, _numkeys, key, _ttl):
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    async def decr(self, key):
        self.values[key] = self.values.get(key, 0) - 1
        return self.values[key]


class _Down:
    async def eval(self, *_args):
        raise ConnectionError("redis is away")


async def test_a_hook_past_its_minute_is_limited_and_the_day_is_not_spent(monkeypatch):
    monkeypatch.setattr(hook_settings, "datastore_hook_deliveries_per_minute", 2)
    counters = _Counters()
    limiter = HookLimiter(redis=counters)
    hook_id = uuid4()

    answers = [await limiter.allow(hook_id) for _ in range(4)]

    assert answers == [HookAllowance.ALLOWED] * 2 + [HookAllowance.LIMITED] * 2
    assert counters.values[f"hook:deliveries:day:{hook_id}"] == 2
    # Another hook has its own windows.
    assert await limiter.allow(uuid4()) is HookAllowance.ALLOWED


async def test_the_limiter_fails_closed_when_redis_is_away():
    assert await HookLimiter(redis=_Down()).allow(uuid4()) is HookAllowance.UNAVAILABLE


# ------------------------------------------------- the order of refusals


class _Uow:
    def __init__(self, stored) -> None:
        self.stored = stored
        self.writes: list[object] = []
        self.session = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def execute(self, statement):
        if isinstance(statement, Select):
            return SimpleNamespace(first=lambda: self.stored)
        self.writes.append(statement)
        return None

    async def commit(self) -> None:
        return None


class _Limiter:
    def __init__(self, answer: HookAllowance = HookAllowance.ALLOWED) -> None:
        self.answer = answer
        self.asked = 0

    async def allow(self, _hook_id):
        self.asked += 1
        return self.answer


SECRET = "lmh_the-secret"


async def _admit(stored, *, limiter=None, content_type="application/json", body=b"{}"):
    uow = _Uow(stored)
    limiter = limiter or _Limiter()
    try:
        result = await admit_delivery(
            lambda: uow,
            hook_id=uuid4(),
            key=SECRET,
            content_type=content_type,
            raw_body=body,
            limiter=limiter,
        )
    except HookDeliveryRefused as refused:
        return refused, uow, limiter
    return result, uow, limiter


async def test_an_unknown_hook_or_wrong_secret_is_not_admitted_and_nothing_is_kept():
    for stored in (None, (secret_hash("another"), True)):
        result, uow, limiter = await _admit(stored)

        assert result is None
        assert uow.writes == []
        assert limiter.asked == 0


async def test_a_proven_delivery_is_parsed():
    result, uow, _ = await _admit((secret_hash(SECRET), True), body=b'{"a": 1}')

    assert result == {"a": 1}
    assert uow.writes == []


async def test_a_switched_off_hook_refuses_before_spending_its_allowance():
    refused, uow, limiter = await _admit((secret_hash(SECRET), False))

    assert refused.status_code == 403 and "switched off" in refused.reason
    assert limiter.asked == 0
    assert len(uow.writes) == 1  # kept as the hook's last error


@pytest.mark.parametrize(
    ("allowance", "status"),
    [(HookAllowance.LIMITED, 429), (HookAllowance.UNAVAILABLE, 503)],
)
async def test_a_hook_over_its_limit_is_told_to_retry(allowance, status):
    refused, uow, _ = await _admit(
        (secret_hash(SECRET), True), limiter=_Limiter(allowance)
    )

    assert refused.status_code == status
    assert len(uow.writes) == 1


async def test_a_body_over_the_hook_cap_is_refused_with_the_cap(monkeypatch):
    monkeypatch.setattr(hook_settings, "datastore_hook_body_max_bytes", 2048)

    refused, _, _ = await _admit((secret_hash(SECRET), True), body=b" " * 2049)

    assert refused.status_code == 413 and "2 KB" in refused.reason


async def test_a_form_post_is_refused_before_its_body_is_parsed():
    refused, _, _ = await _admit(
        (secret_hash(SECRET), True),
        content_type="application/x-www-form-urlencoded",
        body=b"a=1",
    )

    assert refused.status_code == 415
