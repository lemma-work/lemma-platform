"""What a pod's own assistant is called, wherever a person reads it.

The rule has two answers — the pod's name, or the agent's own — and two callers
need it: a group that is addressing the bot, and an email ``From`` header. The
assistant's row name is ``pod_default``, which is neither answer, so what is
worth pinning is that the pod, and not that identifier, decides.

The pod table's read is faked rather than patched: a double installed inside the
module under test certifies the half the test did not write (``make lint``
counts them), and what is under test here is which name wins, not how the read
is spelled.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.services.group_names import sender_name_for

pytestmark = pytest.mark.asyncio


class _Scalar:
    """One ``scalar_one_or_none()`` answer."""

    def __init__(self, value: str | None) -> None:
        self._value = value

    def scalar_one_or_none(self) -> str | None:
        return self._value


class _PodRow:
    """The pod table's read, answered without a database — and counted."""

    def __init__(self, name: str | None) -> None:
        self.name = name
        self.reads = 0

    async def execute(self, *_args, **_kwargs) -> _Scalar:
        self.reads += 1
        return _Scalar(self.name)


def _uow(session: _PodRow):
    return SimpleNamespace(session=session)


async def test_the_pods_own_assistant_is_named_by_its_pod():
    """`Sales`, not `Lem` and not `pod_default`.

    This is the name the email ``From`` header was missing: the pod's assistant
    went out as the platform's word for it, beside a person's name and the
    product, which is the header that was reported.
    """
    assert (
        await sender_name_for(
            _uow(_PodRow("Sales")),
            is_pod_default=True,
            agent_name="Lem",
            pod_id=uuid4(),
        )
        == "Sales"
    )


async def test_a_named_agent_keeps_its_own_name():
    """One agent, one name — and no read for a pod it is not answering for."""
    pods = _PodRow("Sales")
    assert (
        await sender_name_for(
            _uow(pods), is_pod_default=False, agent_name="Priya", pod_id=uuid4()
        )
        == "Priya"
    )
    assert pods.reads == 0


async def test_a_pod_that_no_longer_resolves_names_nobody():
    """None rather than the assistant's stored name, which is an identifier.

    The caller falls back to the deployment's own name, which is at least true
    of the deployment. ``pod_default`` in a ``From`` line is not a name at all.
    """
    assert (
        await sender_name_for(
            _uow(_PodRow(None)),
            is_pod_default=True,
            agent_name="pod_default",
            pod_id=uuid4(),
        )
        is None
    )
