"""Collections that forget instead of growing.

A memo of "work already done for this id" is the easiest unbounded structure to
write: it is correct, it is fast, and nothing about it looks like a leak until a
long-lived process has seen enough ids. The API pods this serves run for hours
and see one entry per organization, pod, table or conversation.

Forgetting an entry is safe for every caller here, because each memo guards work
that is idempotent -- a re-provisioned role scope or a re-ensured index costs a
round trip, not correctness. That is the property to check before reaching for
these: a bounded memo must be an optimisation, never a source of truth.

These are the only sanctioned shape for process-lifetime state keyed by
anything that grows with traffic (``scripts/check_memory_hazards.py`` enforces
it). Data caching still belongs in Redis; these hold objects that cannot leave
the process -- clients, engines, handles, memos of idempotent work.

A collection given a ``name`` registers itself, so the memory sampler and its
on-demand dump can report every bounded structure in the process without each
one being wired up by hand. The registry holds weak references: a collection
that belongs to a short-lived object disappears with it.
"""

from __future__ import annotations

import weakref
from collections import OrderedDict
from typing import Callable, Generic, Hashable, Iterator, TypeVar

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")

_registry: weakref.WeakSet[_Bounded] = weakref.WeakSet()


class _Bounded:
    """Shared bookkeeping: the cap, the eviction count, the registry entry."""

    name: str | None
    _maxsize: int
    evictions: int

    def _init_bounded(self, maxsize: int, name: str | None) -> None:
        if maxsize <= 0:
            raise ValueError("maxsize must be positive")
        self._maxsize = maxsize
        self.name = name
        self.evictions = 0
        if name is not None:
            _registry.add(self)

    @property
    def maxsize(self) -> int:
        return self._maxsize

    def stats(self) -> dict[str, int | str | None]:
        return {
            "name": self.name,
            "entries": len(self),  # type: ignore[arg-type]
            "maxsize": self._maxsize,
            "evictions": self.evictions,
        }


def registered_collections() -> list[dict[str, int | str | None]]:
    """Stats for every named bounded collection alive in this process."""
    return sorted(
        (collection.stats() for collection in list(_registry)),
        key=lambda stats: str(stats["name"]),
    )


class BoundedSet(_Bounded, Generic[K]):
    """A set that evicts its least recently added member past ``maxsize``."""

    def __init__(self, maxsize: int, *, name: str | None = None) -> None:
        self._items: OrderedDict[K, None] = OrderedDict()
        self._init_bounded(maxsize, name)

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
            self.evictions += 1

    def discard(self, item: K) -> None:
        self._items.pop(item, None)

    def clear(self) -> None:
        self._items.clear()


class BoundedDict(_Bounded, Generic[K, V]):
    """A mapping that evicts its least recently used key past ``maxsize``.

    "Used" means set, and also read when ``touch_on_get`` is true -- the LRU a
    client or engine cache wants, where a hot entry must outlive a cold one
    regardless of which was created first.

    ``on_evict`` receives each value pushed out by the cap (not ones removed
    with ``pop``/``clear``, which the caller already holds). An evicted client
    or engine owns sockets that garbage collection will not close promptly, so
    a cache of them must close what it drops.
    """

    def __init__(
        self,
        maxsize: int,
        *,
        name: str | None = None,
        touch_on_get: bool = False,
        on_evict: Callable[[K, V], None] | None = None,
    ) -> None:
        self._items: OrderedDict[K, V] = OrderedDict()
        self._touch_on_get = touch_on_get
        self._on_evict = on_evict
        self._init_bounded(maxsize, name)

    def __contains__(self, key: object) -> bool:
        return key in self._items

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[K]:
        return iter(self._items)

    def items(self) -> list[tuple[K, V]]:
        return list(self._items.items())

    def values(self) -> list[V]:
        return list(self._items.values())

    def get(self, key: K, default: V | None = None) -> V | None:
        if key not in self._items:
            return default
        if self._touch_on_get:
            self._items.move_to_end(key)
        return self._items[key]

    def __getitem__(self, key: K) -> V:
        value = self._items[key]
        if self._touch_on_get:
            self._items.move_to_end(key)
        return value

    def __setitem__(self, key: K, value: V) -> None:
        self._items[key] = value
        self._items.move_to_end(key)
        while len(self._items) > self._maxsize:
            evicted_key, evicted = self._items.popitem(last=False)
            self.evictions += 1
            if self._on_evict is not None:
                self._on_evict(evicted_key, evicted)

    def __delitem__(self, key: K) -> None:
        del self._items[key]

    def setdefault(self, key: K, default: V) -> V:
        if key not in self._items:
            self[key] = default
        return self._items[key]

    def pop(self, key: K, default: V | None = None) -> V | None:
        return self._items.pop(key, default)

    def clear(self) -> None:
        self._items.clear()
