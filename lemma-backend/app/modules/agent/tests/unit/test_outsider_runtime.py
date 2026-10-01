"""A stranger's turn never runs where a shell holds the member's token.

A run on Agent Host gives a coding tool its own shell, and a Lemma token minted
for the conversation's user -- on a run answering somebody outside the pod, the
member who answers for the group. Lemma's own tools are anonymous on such a
run; a shell command is not one of them. So the run is moved onto a model that
runs in this process, or refused, and the Agent Host payload will not mint the
token for it either way.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.domain.errors import DomainError
from app.modules.agent.domain.outsiders import OutsiderRunRefused
from app.modules.agent.domain.value_objects import AgentRuntimeConfig, HarnessKind
from app.modules.agent.infrastructure.harnesses.remote_payload import mcp_payload
from app.modules.agent.services.outsider_runtime import in_process_runtime

pytestmark = pytest.mark.unit

AGENT_HOST = SimpleNamespace(harness_kind=HarnessKind.HARNESS)
IN_PROCESS = SimpleNamespace(harness_kind=HarnessKind.LEMMA)
ORGANIZATION = AgentRuntimeConfig(profile_id="organization-default")
SYSTEM = AgentRuntimeConfig(profile_id="system-default")


def _resolving(answers: dict[str, object]):
    async def resolve(config: AgentRuntimeConfig):
        answer = answers[config.profile_id]
        if isinstance(answer, Exception):
            raise answer
        return answer

    return resolve


async def test_a_run_that_already_runs_here_keeps_its_runtime():
    chosen = await in_process_runtime(
        IN_PROCESS, fallbacks=[ORGANIZATION], resolve=_resolving({})
    )

    assert chosen is IN_PROCESS


async def test_a_run_bound_for_agent_host_takes_the_first_default_that_runs_here():
    chosen = await in_process_runtime(
        AGENT_HOST,
        fallbacks=[ORGANIZATION, SYSTEM],
        resolve=_resolving(
            {"organization-default": AGENT_HOST, "system-default": IN_PROCESS}
        ),
    )

    assert chosen is IN_PROCESS


async def test_a_default_that_cannot_be_resolved_is_passed_over():
    chosen = await in_process_runtime(
        AGENT_HOST,
        fallbacks=[ORGANIZATION, SYSTEM],
        resolve=_resolving(
            {
                "organization-default": DomainError("retired"),
                "system-default": IN_PROCESS,
            }
        ),
    )

    assert chosen is IN_PROCESS


async def test_with_nothing_that_runs_here_the_stranger_is_refused():
    with pytest.raises(OutsiderRunRefused):
        await in_process_runtime(
            AGENT_HOST,
            fallbacks=[ORGANIZATION],
            resolve=_resolving({"organization-default": AGENT_HOST}),
        )


class _NoTokens:
    """The workspace service that would mint the run's Lemma token."""

    async def get_env_vars(self, **_kwargs):
        raise AssertionError("a token was minted for a stranger's run")

    async def close(self) -> None:
        return None


async def test_agent_host_is_never_handed_a_token_for_a_strangers_run():
    stranger_run = SimpleNamespace(
        answers_outsider=True,
        user_id=uuid4(),
        pod_id=uuid4(),
        org_id=uuid4(),
        agent_name="pod_default",
    )

    with pytest.raises(OutsiderRunRefused):
        await mcp_payload(
            agent_run_id=uuid4(),
            conversation_id=uuid4(),
            ctx=stranger_run,
            options=SimpleNamespace(),
            workspace_service=_NoTokens(),
        )
