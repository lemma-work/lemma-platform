"""Domain entities must not resolve a signature on every instantiation.

`AggregateRoot._domain_events` was `PrivateAttr(default_factory=list)`. A
private attribute's default -- unlike a normal `Field`'s, which is resolved once
when the schema is built -- is resolved again for *every instance*, and pydantic
decides whether the factory wants the validated data by calling
`inspect.signature` on it. Nothing caches that, and `list` is a C builtin, the
most expensive kind of object to introspect.

The result was 63us to build one domain entity against 2.6us without it. Listing
a pod's files builds an entity per file, so a large pod blocked the API event
loop for up to 1.7 seconds, and that single line was 75% of all loop-stall time
measured in production over a week.

The fix is `default=[]`, which pydantic copies into each instance. It reads like
the classic shared-mutable-default bug and is not one -- `test_default_is_not_shared`
is the proof -- so these tests exist to stop it being "fixed" back.
`scripts/check_io_hygiene.py` fails the build on the static spelling; this file
pins the behaviour the spelling exists to buy.
"""

from __future__ import annotations

import copy
import pickle

import pydantic._internal._fields as pydantic_fields
import pytest

from app.core.domain.aggregate import AggregateRoot
from app.modules.function.domain.entities import FunctionRunEntity


class _Aggregate(AggregateRoot):
    """Minimal concrete aggregate: the base class is what is under test."""

    name: str = "x"


def _make_function_run() -> FunctionRunEntity:
    from uuid import uuid7

    return FunctionRunEntity(function_id=uuid7(), user_id=uuid7())


@pytest.fixture
def signature_resolutions(monkeypatch: pytest.MonkeyPatch):
    """Count pydantic's per-default signature introspections.

    Patching a pydantic internal is deliberate. The entire value of `default=[]`
    is that this function is never reached, so the test has to watch the thing
    the fix avoids. If pydantic renames it the patch fails loudly, which is the
    correct outcome: the assumption this fix rests on would no longer hold.
    """
    calls = {"n": 0}
    original = pydantic_fields.takes_validated_data_argument

    def counting(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(pydantic_fields, "takes_validated_data_argument", counting)
    return calls


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(_Aggregate, id="aggregate-root"),
        pytest.param(_make_function_run, id="function-run"),
    ],
)
def test_construction_resolves_no_signatures(signature_resolutions, build) -> None:
    """Building an entity must not introspect a default factory. Ever."""
    build()  # warm: schema construction is allowed to resolve defaults once
    signature_resolutions["n"] = 0

    for _ in range(50):
        build()

    assert signature_resolutions["n"] == 0, (
        f"{signature_resolutions['n']} signature resolutions across 50 "
        "instantiations. A `default_factory` came back on a PrivateAttr; use "
        "`default=[]` instead (see this module's docstring)."
    )


def test_default_is_not_shared() -> None:
    """`default=[]` is safe only because pydantic copies it per instance."""
    first, second = _Aggregate(), _Aggregate()

    assert first._domain_events is not second._domain_events

    first.add_event("event")  # type: ignore[arg-type]

    assert first.has_pending_events()
    assert second._domain_events == []
    assert not second.has_pending_events()


def test_default_does_not_leak_into_the_class() -> None:
    """A third instance must still start empty after two others were mutated."""
    first = _Aggregate()
    first.add_event("event")  # type: ignore[arg-type]
    first.collect_events()

    second = _Aggregate()
    second.add_event("other")  # type: ignore[arg-type]

    assert _Aggregate()._domain_events == []


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(lambda: _Aggregate(), id="init"),
        pytest.param(lambda: _Aggregate.model_construct(), id="model-construct"),
        pytest.param(lambda: _Aggregate.model_validate({}), id="model-validate"),
        pytest.param(lambda: _Aggregate().model_copy(), id="model-copy"),
        pytest.param(lambda: _Aggregate().model_copy(deep=True), id="model-copy-deep"),
        pytest.param(lambda: copy.deepcopy(_Aggregate()), id="deepcopy"),
        pytest.param(lambda: pickle.loads(pickle.dumps(_Aggregate())), id="pickle"),
    ],
)
def test_every_construction_path_gets_its_own_list(build) -> None:
    """The shared-default risk, checked on every way an aggregate comes into being.

    `default=[]` is only safe because pydantic copies it per instance. Ordinary
    construction is the obvious path; `model_construct` (used by repositories
    that skip validation), `model_copy` and unpickling are the ones where a
    shared list would actually leak between aggregates unnoticed.
    """
    first, second = build(), build()

    assert first._domain_events is not second._domain_events

    first.add_event("event")  # type: ignore[arg-type]

    assert second._domain_events == []
    assert _Aggregate()._domain_events == []
