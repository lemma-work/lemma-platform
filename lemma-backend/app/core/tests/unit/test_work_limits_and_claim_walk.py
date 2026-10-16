"""Work limits overrides, class settings parsing, and the claim's candidate walk."""

from __future__ import annotations

import json
from uuid import UUID, uuid4

import pytest

from app.core.infrastructure.work import config as work_config
from app.core.infrastructure.work.claim import (
    Candidate,
    fair_share,
    namespace_budget,
    take,
)
from app.core.infrastructure.work.config import (
    CLASS_DEFAULTS,
    class_slots,
    default_max_running,
    enabled_classes,
)
from app.core.infrastructure.work.limits import (
    ConfiguredWorkLimitPort,
    parse_overrides,
    select_override,
)
from app.core.infrastructure.work.rates import DEFAULT_RATES
from app.core.infrastructure.work.types import WorkClass, validate_namespace

POD = UUID("11111111-1111-1111-1111-111111111111")
_INTERACTIVE_CAP = CLASS_DEFAULTS[WorkClass.INTERACTIVE].max_running
ORG = UUID("22222222-2222-2222-2222-222222222222")


def _rules(*entries: dict[str, object]) -> str:
    return json.dumps(list(entries))


class _Directory:
    def __init__(self, slug: str) -> None:
        self.slug = slug

    async def organization_of(self, ns: str) -> tuple[UUID, str] | None:
        return (ORG, self.slug) if ns == f"pod:{POD}" else None


# --- override parsing and selection ---------------------------------------


def test_malformed_overrides_yield_no_rules() -> None:
    assert parse_overrides("not json") == ()
    assert parse_overrides('{"ns": "system"}') == ()
    assert parse_overrides("") == ()


def test_entries_without_a_key_or_a_limit_are_skipped() -> None:
    raw = _rules(
        {"max_running": {"events": 1}},
        {"pod_id": str(POD)},
        {"pod_id": str(POD), "max_running": {"nope": 3, "events": "x"}},
        {"pod_id": str(POD), "max_running": {"events": 4}},
    )
    rules = parse_overrides(raw)
    assert len(rules) == 1
    assert dict(rules[0].max_running) == {WorkClass.EVENTS: 4}


_SPECIFICITY_ORDER = ["pod_id", "org_id", "slug", "long_prefix", "short_prefix"]
_RULES = {
    "pod_id": {"pod_id": str(POD), "max_running": {"events": 5}},
    "org_id": {"org_id": str(ORG), "max_running": {"events": 4}},
    "slug": {"slug": "acme-eu", "max_running": {"events": 3}},
    "long_prefix": {"slug_prefix": "acme-eu", "max_running": {"events": 2}},
    "short_prefix": {"slug_prefix": "acme", "max_running": {"events": 1}},
}


@pytest.mark.parametrize(
    ("winner", "slug", "cap"),
    [
        ("pod_id", "acme-eu", 5),
        ("org_id", "acme-eu", 4),
        ("slug", "acme-eu", 3),
        ("long_prefix", "acme-eu", 2),
        ("short_prefix", "acme-us", 1),
    ],
)
def test_the_most_specific_rule_wins_regardless_of_order(
    winner: str, slug: str, cap: int
) -> None:
    """Every less specific rule is present too, written *after* the winner."""
    keep = _SPECIFICITY_ORDER[_SPECIFICITY_ORDER.index(winner) :]
    rules = parse_overrides(_rules(*(_RULES[name] for name in keep)))
    chosen = select_override(rules, f"pod:{POD}", (ORG, slug))
    assert chosen is not None
    assert chosen.max_running == ((WorkClass.EVENTS, cap),)


def test_a_tie_goes_to_the_rule_written_last() -> None:
    rules = parse_overrides(
        _rules(
            {"ns": "system", "max_running": {"admin": 1}},
            {"ns": "system", "max_running": {"admin": 2}},
        )
    )
    chosen = select_override(rules, "system", None)
    assert chosen is not None and chosen.max_running == ((WorkClass.ADMIN, 2),)


async def test_slug_rules_need_a_directory_and_org_rules_match_org_namespaces() -> None:
    raw = _rules(
        {"slug": "acme", "max_running": {"interactive": 7}},
        {"org_id": str(ORG), "max_running": {"automation": 9}},
    )
    bare = ConfiguredWorkLimitPort(raw_overrides=raw)
    assert (await bare.limits_for(f"pod:{POD}")).cap(
        WorkClass.INTERACTIVE
    ) == _INTERACTIVE_CAP
    assert (await bare.limits_for(f"org:{ORG}")).cap(WorkClass.AUTOMATION) == 9

    known = ConfiguredWorkLimitPort(raw_overrides=raw, directory=_Directory("acme"))
    limits = await known.limits_for(f"pod:{POD}")
    # Both match; the org id is more specific than the slug.
    assert limits.cap(WorkClass.AUTOMATION) == 9
    assert limits.cap(WorkClass.INTERACTIVE) == _INTERACTIVE_CAP
    assert limits.cap(WorkClass.EVENTS) == CLASS_DEFAULTS[WorkClass.EVENTS].max_running


async def test_rates_are_carried_from_overrides() -> None:
    raw = _rules(
        {
            "ns": "system",
            "rates": {"email_out": {"limit": 200, "per_seconds": 3600}, "bad": {}},
        }
    )
    limits = await ConfiguredWorkLimitPort(raw_overrides=raw).limits_for("system")
    assert set(limits.rates) == set(DEFAULT_RATES)
    assert limits.rates["email_out"].limit == 200
    assert limits.rates["llm_filter"] == DEFAULT_RATES["llm_filter"]


# --- class settings ---------------------------------------------------------


def test_class_settings_overrides_apply_per_class(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        work_config.work_settings, "work_slots_json", '{"events": 99, "x": 1}'
    )
    monkeypatch.setattr(
        work_config.work_settings, "work_max_running_json", '{"admin": 3}'
    )
    assert class_slots(WorkClass.EVENTS) == 99
    assert class_slots(WorkClass.ADMIN) == CLASS_DEFAULTS[WorkClass.ADMIN].slots
    assert default_max_running(WorkClass.ADMIN) == 3


def test_enabled_classes_parsing() -> None:
    assert enabled_classes("") == list(WorkClass)
    assert enabled_classes("none") == []
    assert enabled_classes(" Ingestion, events,ingestion ") == [
        WorkClass.INGESTION,
        WorkClass.EVENTS,
    ]
    with pytest.raises(ValueError, match="unknown class 'bulk'"):
        enabled_classes("events,bulk")


def test_namespaces_are_validated() -> None:
    assert validate_namespace(f"pod:{POD}") == f"pod:{POD}"
    assert validate_namespace("system") == "system"
    upper = "pod:ABCDEF12-1111-1111-1111-111111111111"
    for bad in ("pod:abc", f"pods:{POD}", upper, ""):
        with pytest.raises(ValueError):
            validate_namespace(bad)


# --- the candidate walk -----------------------------------------------------


def _c(key: str | None = None, limit: int | None = None) -> Candidate:
    return Candidate(uuid4(), "k", key, limit)


def test_take_stops_at_the_budget() -> None:
    candidates = [_c() for _ in range(5)]
    assert take(candidates, 2, {}).taken == [c.id for c in candidates[:2]]


def test_a_busy_key_is_skipped_without_blocking_what_follows() -> None:
    busy, free_a, free_b = _c("conv:1", 1), _c(), _c()
    assert take([busy, free_a, free_b], 2, {"conv:1": 0}).taken == [
        free_a.id,
        free_b.id,
    ]


def test_a_key_is_taken_only_as_far_as_its_room() -> None:
    first, second, third = _c("k", 2), _c("k", 2), _c("k", 2)
    unkeyed = _c()
    taken = take([first, second, third, unkeyed], 10, {"k": 2}).taken
    assert taken == [first.id, second.id, unkeyed.id]


def test_priority_order_is_preserved() -> None:
    candidates = [_c(), _c("a", 1), _c(), _c("a", 1)]
    taken = take(candidates, 3, {"a": 1}).taken
    assert taken == [candidates[0].id, candidates[1].id, candidates[2].id]


@pytest.mark.parametrize(
    ("quantum", "free", "ready", "share"),
    [(2, 32, 1, 32), (2, 32, 2, 16), (2, 32, 32, 2), (2, 4, 2, 2), (2, 1, 1, 2)],
)
def test_fair_share_is_work_conserving_with_quantum_as_the_floor(
    quantum: int, free: int, ready: int, share: int
) -> None:
    assert fair_share(quantum=quantum, free=free, ready=ready) == share


def test_namespace_budget_respects_cap_room_and_free_slots() -> None:
    assert namespace_budget(share=8, room=3, free=10) == 3
    assert namespace_budget(share=8, room=30, free=5) == 5
    assert namespace_budget(share=2, room=0, free=5) == 0
