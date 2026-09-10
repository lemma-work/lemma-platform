"""The configuration-backed usage limit port.

`_parse_overrides` turns deployment settings into override rules keyed on an
organization's slug — exactly, or by prefix, so a family of organizations can
share one cap. Malformed input must yield no rules rather than take spending
decisions down with it: limits gate admission, so a crash here is an outage.
"""

from __future__ import annotations

from app.modules.usage.infrastructure.configured_usage_limit_port import (
    _limit_for,
    _parse_overrides,
)


def test_parse_overrides_reads_slug_limit_pairs():
    rules = _parse_overrides(
        '[{"slug": "acme", "monthly_limit_usd": 5}, '
        '{"slug_prefix": "lab-", "monthly_limit_usd": 0}]'
    )
    assert ("acme", 5.0, False) in rules
    assert ("lab-", 0.0, True) in rules


def test_parse_overrides_drops_malformed_entries():
    rules = _parse_overrides(
        '[{"slug": "", "monthly_limit_usd": 1}, '
        '{"monthly_limit_usd": 2}, '
        '{"slug": "acme"}, '
        '{"slug": "ok", "monthly_limit_usd": 3}, '
        '"junk"]'
    )
    assert rules == (("ok", 3.0, False),)


def test_parse_overrides_survives_garbage():
    assert _parse_overrides("") == ()
    assert _parse_overrides("not json") == ()
    assert _parse_overrides('{"slug": "acme"}') == ()


def test_limit_for_prefers_the_exact_handle_over_any_prefix():
    """Specificity decides, not authoring order.

    The broad rule is written *last* here, which under an order-wins rule would
    have taken the cap away from the organization the exact rule names -- and
    said nothing about it.
    """
    rules = (("acme", 5.0, False), ("acme", 9.0, True))
    assert _limit_for("acme", rules) == 5.0


def test_limit_for_prefers_the_longer_prefix():
    rules = (("lab-eu-", 1.0, True), ("lab-", 7.0, True))
    assert _limit_for("lab-eu-rat", rules) == 1.0
    assert _limit_for("lab-us-rat", rules) == 7.0


def test_limit_for_settles_a_genuine_tie_by_position():
    rules = (("lab-", 1.0, True), ("lab-", 2.0, True))
    assert _limit_for("lab-rat", rules) == 2.0


def test_limit_for_matches_exact_and_prefix():
    rules = (("acme", 5.0, False), ("lab-", 2.0, True))
    assert _limit_for("acme", rules) == 5.0
    assert _limit_for("acme-corp", rules) is None
    assert _limit_for("lab-rat", rules) == 2.0
    assert _limit_for(None, rules) is None
