from __future__ import annotations

import re

import pytest

from app.modules.datastore.api.record_query import (
    RECORD_FILTER_DESCRIPTION,
    parse_record_filters,
    parse_record_sorts,
)
from app.modules.datastore.api.schemas.datastore_schemas import RecordFilterOperator
from app.modules.datastore.domain.errors import DatastoreValidationError


def test_parse_record_filters_accepts_json_filter_clauses():
    result = parse_record_filters(
        [
            '{"field":"email_thread_id","op":"eq","value":"codex-smoke-no-match-5"}',
            '{"field":"priority","op":"ne","value":"low"}',
            '{"field":"amount","op":"gte","value":100}',
            '{"field":"archived","op":"eq","value":false}',
        ],
    )

    assert result == [
        ("email_thread_id", "eq", "codex-smoke-no-match-5"),
        ("priority", "ne", "low"),
        ("amount", "gte", 100),
        ("archived", "eq", False),
    ]


def test_parse_record_filters_accepts_in_with_a_list_value():
    result = parse_record_filters(
        ['{"field":"status","op":"in","value":["new","open","pending"]}'],
    )

    assert result == [("status", "in", ["new", "open", "pending"])]


def test_parse_record_filters_rejects_unknown_operator():
    with pytest.raises(DatastoreValidationError, match="Unsupported filter operator"):
        parse_record_filters(['{"field":"status","op":"nope","value":"x"}'])


def test_parse_record_filters_rejects_bad_json():
    with pytest.raises(DatastoreValidationError, match="Invalid filter parameter"):
        parse_record_filters(["{not json"])


def test_parse_record_filters_rejects_shorthand():
    with pytest.raises(DatastoreValidationError, match="Invalid filter parameter"):
        parse_record_filters(["status = 'new'"])


def test_parse_record_sorts_accepts_json_then_none():
    assert parse_record_sorts(['{"field":"created_at","direction":"desc"}']) == [
        ("created_at", "desc")
    ]
    assert parse_record_sorts(None) is None


def test_parse_record_sorts_accepts_json_sort_clauses():
    assert parse_record_sorts(
        [
            '{"field":"updated_at","direction":"desc"}',
            '{"field":"priority","direction":"asc"}',
            '{"field":"name"}',
        ]
    ) == [
        ("updated_at", "desc"),
        ("priority", "asc"),
        ("name", "asc"),
    ]


def test_parse_record_sorts_rejects_bad_json():
    with pytest.raises(DatastoreValidationError, match="Invalid sort parameter"):
        parse_record_sorts(["{bad"])


class TestTheFilterParameterDocumentsWhatItAccepts:
    """The description is what the OpenAPI spec, both SDKs and the docs render.
    Hand-written, it listed eight of the nine operators -- `in` was implemented,
    named in the rejection message, and invisible to everyone reading a client.
    """

    def test_every_operator_the_parser_takes_is_named(self) -> None:
        for operator in RecordFilterOperator:
            assert f"`{operator.value}`" in RECORD_FILTER_DESCRIPTION

    def test_the_list_is_the_enum_rather_than_a_copy_of_it(self) -> None:
        """A copy is what drifted. Pinning the rendered list to the enum means
        a tenth operator documents itself."""
        listed = re.search(
            r"Allowed operators are: (.+?)\. ", RECORD_FILTER_DESCRIPTION
        )

        assert listed is not None
        assert listed.group(1) == ", ".join(
            f"`{operator.value}`" for operator in RecordFilterOperator
        )

    def test_the_wildcards_in_a_like_pattern_are_explained(self) -> None:
        """`%` and `_` are wildcards, so a caller filtering on a literal value
        containing `_` -- ordinary in identifiers and paths -- silently gets
        extra rows. Nothing said so, and nothing said how to escape one."""
        assert "`%`" in RECORD_FILTER_DESCRIPTION
        assert "`_`" in RECORD_FILTER_DESCRIPTION
        assert "backslash" in RECORD_FILTER_DESCRIPTION
