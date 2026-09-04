"""Memos must forget, and forgetting must be safe.

Each caller uses these to remember work already done. The property that makes
eviction safe is that the work is idempotent -- so these tests pin the eviction
order rather than the presence of any one entry.
"""

from __future__ import annotations

import pytest

from app.core.bounded import BoundedDict, BoundedSet


class TestBoundedSet:
    def test_it_forgets_the_oldest_first(self) -> None:
        seen = BoundedSet[int](3)

        for value in range(5):
            seen.add(value)

        assert len(seen) == 3
        assert 0 not in seen and 1 not in seen
        assert 2 in seen and 3 in seen and 4 in seen

    def test_re_adding_keeps_an_entry_alive(self) -> None:
        seen = BoundedSet[str](2)
        seen.add("a")
        seen.add("b")

        seen.add("a")
        seen.add("c")

        # "b" was the least recently added, so it goes and "a" survives.
        assert "a" in seen and "c" in seen
        assert "b" not in seen

    def test_a_zero_bound_is_a_bug_not_a_disabled_cache(self) -> None:
        with pytest.raises(ValueError):
            BoundedSet[int](0)


class TestBoundedDict:
    def test_it_forgets_the_oldest_first(self) -> None:
        held = BoundedDict[int, str](2)

        for value in range(4):
            held[value] = str(value)

        assert len(held) == 2
        assert held.get(0) is None
        assert held.get(3) == "3"

    def test_setdefault_does_not_disturb_an_existing_entry(self) -> None:
        held = BoundedDict[str, list](2)
        held["a"] = ["first"]

        assert held.setdefault("a", ["second"]) == ["first"]

    def test_pop_removes_and_returns(self) -> None:
        held = BoundedDict[str, int](2)
        held["a"] = 1

        assert held.pop("a") == 1
        assert "a" not in held
        assert held.pop("missing", -1) == -1
