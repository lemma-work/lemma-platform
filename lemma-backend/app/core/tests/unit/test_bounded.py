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

        existing = held.setdefault("a", ["second"])

        assert existing == ["first"]

    def test_pop_removes_and_returns(self) -> None:
        held = BoundedDict[str, int](2)
        held["a"] = 1

        popped = held.pop("a")
        missing = held.pop("missing", -1)

        assert popped == 1
        assert "a" not in held
        assert missing == -1


class TestLruAndEviction:
    def test_touch_on_get_keeps_a_hot_entry(self) -> None:
        cache = BoundedDict[str, int](2, touch_on_get=True)
        cache["a"] = 1
        cache["b"] = 2
        assert cache.get("a") == 1
        cache["c"] = 3
        assert "a" in cache and "b" not in cache

    def test_on_evict_receives_what_the_cap_pushed_out(self) -> None:
        evicted: list[tuple[str, int]] = []
        cache = BoundedDict[str, int](
            1, on_evict=lambda key, value: evicted.append((key, value))
        )
        cache["a"] = 1
        cache["b"] = 2
        cache.pop("b")
        assert evicted == [("a", 1)]
        assert cache.evictions == 1

    def test_named_collections_register_and_unnamed_do_not(self) -> None:
        from app.core.bounded import registered_collections

        named = BoundedSet[int](4, name="test.bounded.registry")
        named.add(1)
        BoundedSet[int](4).add(1)
        stats = [
            s for s in registered_collections() if s["name"] == "test.bounded.registry"
        ]
        assert stats == [
            {
                "name": "test.bounded.registry",
                "entries": 1,
                "maxsize": 4,
                "evictions": 0,
            }
        ]
