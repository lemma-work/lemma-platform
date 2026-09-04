"""Who bills, and who must not bill twice.

Metering used to be five hand-rolled copies of the same twenty lines, and the
next helper would have been free. `billed` makes it a property of asking for a
model rather than of remembering to account for one -- which puts a new
obligation in its place: exactly one thing may bill for any given request.

The run's own model is the one that must stay unwrapped. Its spend already
arrives as harness USAGE events and is settled once by the finalizer, so
wrapping it would write a second row for tokens that are already on the first.
Everything else -- the summarizer, the vision model, the title model, the
schedule filter, the README polisher -- runs on the deployment's credentials
with nothing else counting, and must be wrapped.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic_ai.models import Model
from pydantic_ai.models.wrapper import WrapperModel

from app.modules.agent.services.metered_model import MeteredModel, billed, metered
from app.modules.usage.contracts import UsageReservation
from app.modules.usage.services.usage_context import UsageExecutionContext

pytestmark = pytest.mark.unit


def _context() -> UsageExecutionContext:
    return UsageExecutionContext(
        user_id=uuid4(),
        organization_id=uuid4(),
        pod_id=uuid4(),
        source_type="agent_run",
    )


def _usage(input_tokens: int = 120, output_tokens: int = 30) -> SimpleNamespace:
    return SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=0,
        cache_write_tokens=0,
    )


class _StubModel(Model):
    """A real model that makes no provider call.

    A genuine `Model` rather than a duck type, because `WrapperModel` runs
    `infer_model` on what it is handed and the point of these tests is the
    wrapper's own behaviour, not its rejection of stand-ins.
    """

    @property
    def model_name(self) -> str:
        return "stub-model"

    @property
    def system(self) -> str:
        return "stub"

    async def request(self, messages, model_settings, model_request_parameters):
        raise AssertionError("no test here should reach the provider")


class _NotAModel:
    """What a harness test hands in: enough surface for its own caller, no more."""

    model_name = "not-a-model"


def test_something_that_is_not_a_model_is_handed_back_unwrapped():
    """`WrapperModel` runs `infer_model`, which rejects a stand-in outright.

    Tests hand the harness objects that satisfy only the parts of the protocol
    their caller uses. Something that is not a model makes no provider calls and
    so has nothing to meter -- returning it unchanged is both the safe answer
    and the correct one.
    """
    stub = _NotAModel()

    assert metered(stub, runtime_profile=None, source_type="history_compaction") is stub


async def test_a_metered_model_writes_one_row_per_request():
    recorded: list[dict[str, object]] = []

    async def _record(*, ctx, runtime_profile, usage_data, status, reservation=None):
        del runtime_profile, reservation
        recorded.append(
            {
                "source_type": ctx.source_type,
                "status": status,
                "input_tokens": usage_data.input_tokens,
            }
        )

    wrapper = _wrapper(_record, reservation=None)

    await wrapper._record(SimpleNamespace(usage=_usage()))
    await wrapper._record(SimpleNamespace(usage=_usage(input_tokens=40)))

    assert [row["input_tokens"] for row in recorded] == [120, 40]
    # Attributed to the helper, not to whatever run happens to be ambient.
    assert {row["source_type"] for row in recorded} == {"vision"}


async def test_the_reservation_settles_once_however_many_requests_follow():
    """Consumed by the first row, not by every row.

    A helper makes one model call, so this is the settlement the hand-rolled
    versions performed. A helper that makes two now records twice and settles
    once, where repeating the reservation would hand back allowance that was
    only ever taken.
    """
    settled: list[UsageReservation | None] = []

    async def _record(*, ctx, runtime_profile, usage_data, status, reservation=None):
        del ctx, runtime_profile, usage_data, status
        settled.append(reservation)

    reservation = UsageReservation(
        organization_id=uuid4(), user_id=uuid4(), amount_usd=0.01
    )
    wrapper = _wrapper(_record, reservation=reservation)

    await wrapper._record(SimpleNamespace(usage=_usage()))
    await wrapper._record(SimpleNamespace(usage=_usage()))

    assert settled == [reservation, None]


async def test_a_scope_that_never_ran_hands_its_reservation_back():
    """The zero-request case, which is where a hold would otherwise strand.

    Left alone it survives until the whole window rolls over, quietly shrinking
    that person's allowance in the meantime.
    """
    released: list[UsageReservation] = []

    async def _release(reservation):
        released.append(reservation)

    reservation = UsageReservation(
        organization_id=uuid4(), user_id=uuid4(), amount_usd=0.01
    )

    async def _reserve(**_kwargs):
        return reservation

    async with billed(
        _StubModel(),
        source_type="vision",
        runtime_profile={"profile_id": "system:lemma", "scope": "SYSTEM"},
        context=_context(),
        reserve=_reserve,
        release=_release,
    ) as model:
        assert isinstance(model, MeteredModel)

    assert released == [reservation]


async def test_the_hold_is_returned_even_when_the_work_raises():
    released: list[UsageReservation] = []

    reservation = UsageReservation(
        organization_id=uuid4(), user_id=uuid4(), amount_usd=0.01
    )

    async def _reserve(**_kwargs):
        return reservation

    async def _release(released_reservation):
        released.append(released_reservation)

    with pytest.raises(RuntimeError):
        async with billed(
            _StubModel(),
            source_type="vision",
            runtime_profile=None,
            context=_context(),
            reserve=_reserve,
            release=_release,
        ):
            raise RuntimeError("the provider fell over")

    assert released == [reservation]


def test_the_runs_own_model_is_not_wrapped_by_the_harness():
    """The rule that keeps one request from being billed twice.

    `PydanticAIHarness` wraps the *summarization* model and leaves the run's own
    model alone, because the run's spend already reaches the finalizer as
    harness USAGE events. If this ever reads otherwise, every conversation is
    being charged for its tokens twice.
    """
    import inspect

    from app.modules.agent.infrastructure.harnesses import pydantic_ai as harness

    source = inspect.getsource(harness)

    assert "summarization_model = metered(" in source
    # The only `metered(` call in the harness. A second one would mean some
    # other model grew a wrapper, and the run's own model is the one it would
    # most plausibly be.
    assert source.count("metered(") == 1


def _wrapper(record, *, reservation):
    return MeteredModel(
        _StubModel(),
        runtime_profile=None,
        source_type="vision",
        context=_context(),
        reservation=reservation,
        record=record,
    )


def test_the_wrapper_is_a_pydantic_ai_wrapper_model():
    """So anything handed it -- including code we do not own -- is metered."""
    assert issubclass(MeteredModel, WrapperModel)
