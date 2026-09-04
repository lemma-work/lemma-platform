"""Collections that forget instead of growing.

A memo of "work already done for this id" is the easiest unbounded structure to
write: it is correct, it is fast, and nothing about it looks like a leak until a
long-lived process has seen enough ids. The API pods this serves run for hours
and see one entry per organization, pod, table or conversation.

Forgetting an entry is safe for every caller here, because each memo guards work
that is idempotent -- a re-provisioned role scope or a re-ensured index costs a
round trip, not correctness. That is the property to check before reaching for
these: a bounded memo must be an optimisation, never a source of truth.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Generic, Hashable, Iterator, TypeVar

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")


class BoundedSet(Generic[K]):
    """A set that evicts its least recently added member past ``maxsize``."""

    def __init__(self, maxsize: int) -> None:
        if maxsize <= 0:
            raise ValueError("maxsize must be positive")
        self._maxsize = maxsize
        self._items: OrderedDict[K, None] = OrderedDict()

    def __contains__(self, item: object) -> bool:
        return item in self._items

    def __len__(self) -> int:
        return len(self._items)

    def add(self, item: K) -> None:
        if item in self._items:
            self._items.move_to_end(item)
            return
        self._items[item] = None
        while len(self._items) > self._maxsize:
            self._items.popitem(last=False)

    def discard(self, item: K) -> None:
        self._items.pop(item, None)

    def clear(self) -> None:
        self._items.clear()


class BoundedDict(Generic[K, V]):
    """A mapping that evicts its least recently set key past ``maxsize``."""

    def __init__(self, maxsize: int) -> None:
        if maxsize <= 0:
            raise ValueError("maxsize must be positive")
        self._maxsize = maxsize
        self._items: OrderedDict[K, V] = OrderedDict()

    def __contains__(self, key: object) -> bool:
        return key in self._items

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[K]:
        return iter(self._items)

    def get(self, key: K, default: V | None = None) -> V | None:
        return self._items.get(key, default)

    def __getitem__(self, key: K) -> V:
        return self._items[key]

    def __setitem__(self, key: K, value: V) -> None:
        self._items[key] = value
        self._items.move_to_end(key)
        while len(self._items) > self._maxsize:
            self._items.popitem(last=False)

    def setdefault(self, key: K, default: V) -> V:
        if key not in self._items:
            self[key] = default
        return self._items[key]

    def pop(self, key: K, default: V | None = None) -> V | None:
        return self._items.pop(key, default)

    def clear(self) -> None:
        self._items.clear()
