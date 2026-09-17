"""Every event subscriber's signature has to mean something at run time.

FastStream builds each subscriber's argument model from the handler's
annotations, and it does so lazily — on the first message, inside the worker,
long after import. A name that only exists under ``TYPE_CHECKING`` therefore
imports cleanly, passes ruff, passes the type checker, passes every unit test,
and then fails once per delivery in production with
``PydanticUserError: ... is not fully defined``.

That is not hypothetical. The worker's surface-webhook subscriber was annotated
with a string forward reference to a class kept out of the module to hold the
import budget down, and the whole of surfaces stopped answering: every inbound
message on every platform was dropped, and the only thing that noticed was the
one e2e file that spawns a real worker process.

This is the cheap half of that check. It resolves the annotations the way
FastStream will, at import scope, over every subscriber the registry wires —
so the failure arrives in a unit test, naming the handler, instead of as
silence on five platforms.
"""

from __future__ import annotations

import typing
from collections.abc import Iterator
from typing import Any

import app.events as worker_events


def _subscriber_handlers() -> Iterator[tuple[str, Any]]:
    """Every wired subscriber's underlying function, with a readable name."""
    for subscriber in getattr(worker_events.broker, "subscribers", []) or []:
        for item in getattr(subscriber, "calls", []) or []:
            handler = getattr(item, "handler", None)
            # FastDepends wraps the function; the annotations live on the
            # original, which is what `get_type_hints` has to be given.
            function = (
                getattr(handler, "_original_call", None)
                or getattr(handler, "call", None)
                or handler
            )
            if function is None:
                continue
            yield getattr(function, "__qualname__", repr(function)), function


def test_the_registry_wires_subscribers_at_all() -> None:
    # Guards the guard: if the walk above stops finding handlers, the test below
    # passes over an empty set and proves nothing.
    assert sum(1 for _ in _subscriber_handlers()) > 20


def test_every_subscriber_annotation_resolves_at_runtime() -> None:
    unresolved: list[str] = []
    for name, function in _subscriber_handlers():
        try:
            typing.get_type_hints(function)
        except Exception as error:  # noqa: BLE001 — the point is what it says
            unresolved.append(f"{name}: {type(error).__name__}: {error}")
    assert not unresolved, (
        "subscriber annotations FastStream cannot resolve at run time — import "
        "the name properly, or move the type to a module the handler already "
        "imports:\n  " + "\n  ".join(unresolved)
    )
