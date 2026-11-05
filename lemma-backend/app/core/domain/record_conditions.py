"""Deterministic ``when`` conditions on a record change.

One filter language for everything that follows a pod's record changes: a
DATASTORE trigger and a live socket subscription read the same grammar through
this one evaluator, which is why it lives in core rather than in either module.

A trigger that only matches on table and operation wakes for every write, and a
schedule with an LLM filter then pays for a model call on each one. A match
condition answers the cheap, decidable part of "should this fire" — is the
status approved, did the owner change — before any of that, from the change
alone and with no database read.

The grammar is deliberately small. Every condition is a column name mapped to
one or more operators; conditions AND together, and so do the operators within
one column. There is no OR and no nesting: a rule that needs them is a rule the
LLM filter should be reading, and a boolean tree in JSON is a language nobody
asked for.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

#: The operation a change records, upper-case. A ``str`` enum whose values are
#: these compares equal to them, so a caller passes its own enum.
INSERT = "INSERT"
UPDATE = "UPDATE"
DELETE = "DELETE"


# Operators that ask what the row looks like now. They read the payload, which
# every operation carries.
_VALUE_OPERATORS = ("equals", "not_equals", "in_", "not_in")
# Operators that ask what the row moved away from. Only an UPDATE carries a
# prior image, so nothing else can satisfy them.
_UPDATE_ONLY_OPERATORS = ("changed", "written", "from_")


class ColumnCondition(BaseModel):
    """Operators applied to a single column. All supplied operators must hold."""

    # `extra="forbid"` is load-bearing: a typo'd operator that parsed as an
    # unknown key would make the condition vacuously true and the trigger would
    # fire on everything. Failing at save time is the whole point.
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    equals: JsonValue = None
    not_equals: JsonValue = None
    in_: list[JsonValue] | None = Field(default=None, alias="in")
    not_in: list[JsonValue] | None = None
    # True when the column's value differs from what it was before this write.
    changed: bool | None = None
    # True when this write set the column at all, even to the value it already
    # held. `changed` asks whether the value moved; `written` asks whether
    # somebody touched it.
    written: bool | None = None
    from_: JsonValue = Field(default=None, alias="from")
    to: JsonValue = None

    @model_validator(mode="after")
    def _require_an_operator(self) -> "ColumnCondition":
        # None is a legitimate value to compare against, so "was it supplied"
        # cannot be read off the value — only off the set of supplied fields.
        if not self.model_fields_set:
            raise ValueError(
                "Column condition must set at least one operator "
                "(equals, not_equals, in, not_in, changed, written, from, to)."
            )
        return self

    def supplied(self, operator: str) -> bool:
        return operator in self.model_fields_set

    @property
    def needs_prior_image(self) -> bool:
        """Whether this condition can only ever be satisfied by an UPDATE."""
        return any(self.supplied(name) for name in _UPDATE_ONLY_OPERATORS)

    @property
    def asks_about_movement(self) -> bool:
        """Whether this condition reads the prior image (or the written set).

        A reader that sees only the current row -- a live subscription -- can
        answer value operators and nothing else.
        """
        return self.needs_prior_image or self.supplied("to")

    @property
    def needs_a_written_row(self) -> bool:
        """Whether this condition needs a row that an INSERT or UPDATE produced.

        `to` asks what a value became, which a delete never answers.
        """
        return self.supplied("to")


def parse_match_conditions(value: object) -> dict[str, ColumnCondition]:
    """Parse the `when` block of a DATASTORE config into column conditions.

    A bare scalar is shorthand for equality, so the common case reads as
    ``{"status": "approved"}`` rather than ``{"status": {"equals": "approved"}}``.
    """
    if not isinstance(value, dict):
        raise ValueError("`when` must be an object keyed by column name")
    if not value:
        raise ValueError("`when` must not be empty; omit it to match every row")

    parsed: dict[str, ColumnCondition] = {}
    for column, raw in value.items():
        if not isinstance(column, str) or not column.strip():
            raise ValueError("`when` keys must be column names")
        if isinstance(raw, dict):
            parsed[column.strip()] = ColumnCondition.model_validate(raw)
        else:
            parsed[column.strip()] = ColumnCondition(equals=raw)
    return parsed


def _match_value_operators(
    condition: ColumnCondition, present: bool, actual: object
) -> bool:
    if condition.supplied("equals") and actual != condition.equals:
        return False
    if condition.supplied("not_equals") and actual == condition.not_equals:
        return False
    if condition.supplied("in_") and actual not in (condition.in_ or []):
        return False
    if condition.supplied("not_in") and actual in (condition.not_in or []):
        return False
    # A column the payload does not carry cannot satisfy a value test. This only
    # arises for writes whose event carries a partial row; a full row makes
    # `present` always true.
    if not present and any(condition.supplied(name) for name in _VALUE_OPERATORS):
        return False
    return True


def _claims_the_value_moved(condition: ColumnCondition) -> bool:
    """Whether the condition positively asserts a change happened."""
    return any(
        condition.supplied(name) and getattr(condition, name)
        for name in ("changed", "written")
    )


def _match_on_removed_row(condition: ColumnCondition) -> bool:
    """A delete wrote nothing and has no prior image.

    So no operator that asks about movement can hold. `changed: False` is still
    a real question — "this did not change" — and on a delete it is trivially
    true, which is why this is not a blanket rejection.
    """
    if _claims_the_value_moved(condition):
        return False
    return not (condition.supplied("from_") or condition.supplied("to"))


def _match_on_new_row(condition: ColumnCondition, actual: object) -> bool:
    """An insert has nothing to have moved away from.

    `changed`, `written` and `from` are therefore all false. `to` is the
    exception: a row created already approved did become approved, and making
    authors write a second trigger for that case is the papercut this feature
    exists to remove.
    """
    if _claims_the_value_moved(condition):
        return False
    if condition.supplied("from_"):
        return False
    if condition.supplied("to") and actual != condition.to:
        return False
    return True


def _match_on_updated_row(
    condition: ColumnCondition,
    column: str,
    actual: object,
    *,
    changed: list[str],
    previous: Mapping[str, object] | None,
) -> bool:
    was_written = column in changed
    prior = (previous or {}).get(column)
    # `previous` is narrowed to the written columns, so an untouched column has
    # no entry — which is exactly what distinguishes "did not change" from
    # "changed to the same value".
    did_change = was_written and prior != actual

    if condition.supplied("written") and bool(condition.written) is not was_written:
        return False
    if condition.supplied("changed") and bool(condition.changed) is not did_change:
        return False
    if condition.supplied("from_") and not (was_written and prior == condition.from_):
        return False
    if condition.supplied("to") and not (
        was_written and actual == condition.to and prior != condition.to
    ):
        return False
    return True


def _match_change_operators(
    condition: ColumnCondition,
    column: str,
    actual: object,
    *,
    operation: str,
    changed: list[str],
    previous: Mapping[str, object] | None,
) -> bool:
    if operation == DELETE:
        return _match_on_removed_row(condition)
    if operation == INSERT:
        return _match_on_new_row(condition, actual)
    return _match_on_updated_row(
        condition, column, actual, changed=changed, previous=previous
    )


def evaluate_match_conditions(
    conditions: dict[str, ColumnCondition] | None,
    *,
    operation: str,
    payload: Mapping[str, object] | None,
    changed: list[str] | None = None,
    previous: Mapping[str, object] | None = None,
) -> bool:
    """Decide whether an event satisfies every condition.

    No conditions means match, so an existing trigger keeps firing exactly as
    it did before anyone had this feature.
    """
    if not conditions:
        return True

    row = payload or {}
    written = changed or []
    for column, condition in conditions.items():
        present = column in row
        actual = row.get(column)
        if not _match_value_operators(condition, present, actual):
            return False
        if not _match_change_operators(
            condition,
            column,
            actual,
            operation=operation,
            changed=written,
            previous=previous,
        ):
            return False
    return True
