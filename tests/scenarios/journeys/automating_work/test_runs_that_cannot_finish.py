"""Automating work → a run that cannot finish does not sit there forever.

Every other failure in the product is legible: something went wrong and said so.
A run stuck in RUNNING is the one that is not. Nobody gets an error, nothing
retries, the person watching sees a spinner, and the only way anyone finds out
is by asking why a number never changed.

So the promise is narrow and absolute: after the work behind a run has stopped,
the run is in a terminal state. These drive real functions in a real Docker
sandbox, which is why they carry the sandbox marker.
"""

from __future__ import annotations

import pytest

from harness import capability, covers, journey, proves, scenario
from harness.waiting import eventually

pytestmark = [
    journey("Automating work"),
    capability("Run a function"),
    pytest.mark.sandbox,
]

TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}

#: Far longer than any function this suite runs on purpose. If a run is still
#: going after this, it is not slow — nothing is going to end it.
LONG_ENOUGH_TO_KNOW = 240.0


def _sleeps_for(seconds: int) -> str:
    """A function that does nothing for a long time, and says so when it wakes."""
    return (
        "#input_type_name: Input\n"
        "#output_type_name: Output\n"
        "#function_name: dawdle\n"
        "\n"
        "import asyncio\n"
        "from pydantic import BaseModel\n"
        "\n"
        "class Input(BaseModel):\n"
        "    value: int = 0\n"
        "\n"
        "class Output(BaseModel):\n"
        "    value: int\n"
        "\n"
        "async def dawdle(ctx, data: Input) -> Output:\n"
        f"    await asyncio.sleep({seconds})\n"
        "    return Output(value=data.value)\n"
    )


def _never_returns() -> str:
    """A function that will not yield, so nothing inside it can be interrupted."""
    return (
        "#input_type_name: Input\n"
        "#output_type_name: Output\n"
        "#function_name: spin\n"
        "\n"
        "from pydantic import BaseModel\n"
        "\n"
        "class Input(BaseModel):\n"
        "    value: int = 0\n"
        "\n"
        "class Output(BaseModel):\n"
        "    value: int\n"
        "\n"
        "async def spin(ctx, data: Input) -> Output:\n"
        "    while True:\n"
        "        pass\n"
    )


async def _run_until_settled(alice, pod, name: str, code: str):
    """Start a function and watch it until it stops being in progress."""
    await alice.creates_a_function(in_pod=pod, named=name, code=code)
    started = await alice.api.post(
        f"/pods/{pod['id']}/functions/{name}/runs",
        what=f"alice starting {name!r}",
        json={"input_data": {"value": 1}},
    )
    return await eventually(
        lambda: alice.api.get(
            f"/pods/{pod['id']}/functions/{name}/runs/{started['id']}"
        ),
        lambda run: str(run.get("status")) in TERMINAL,
        describe=f"the run of {name!r} to reach a terminal state",
        timeout=LONG_ENOUGH_TO_KNOW,
    )


@pytest.fixture
async def a_pod(world):
    alice = await world.new_person("alice")
    await alice.creates_an_organization()
    return alice, await alice.creates_a_pod()


@scenario("A function that sleeps past its limit is stopped and marked failed")
@proves("PS-FUNC-012")
@covers("function.run", "function.run.get")
@pytest.mark.timeout(420)
async def test_a_sleeping_function_is_stopped(a_pod):
    alice, pod = a_pod

    settled = await _run_until_settled(
        alice, pod, "dawdle_fn", _sleeps_for(3600)
    )

    assert str(settled.get("status")) == "FAILED", (
        f"a function asleep for an hour finished as {settled.get('status')!r}; "
        f"it should have been stopped: {settled}"
    )
    # And it says why, or the person is left with "failed" and no next step.
    assert settled.get("error") or settled.get("error_message"), (
        f"the run was stopped and records no reason: {settled}"
    )


@scenario("A function that will not yield is stopped too")
@proves("PS-FUNC-012")
@covers("function.run", "function.run.get")
@pytest.mark.timeout(420)
async def test_a_spinning_function_is_stopped(a_pod):
    alice, pod = a_pod

    settled = await _run_until_settled(alice, pod, "spin_fn", _never_returns())

    # A cooperative timeout only stops code that comes back to the loop. This
    # one never does, so stopping it takes the sandbox rather than the runtime —
    # and that is exactly the case where a run gets abandoned in RUNNING.
    assert str(settled.get("status")) in TERMINAL, settled
    assert str(settled.get("status")) != "COMPLETED", (
        f"a function that never returns reported success: {settled}"
    )
