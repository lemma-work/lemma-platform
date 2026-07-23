from __future__ import annotations

import asyncio
from uuid import uuid7

import pytest

from lemma_sdk import FunctionContext, Pod
from lemma_sdk.runtime import (
    FunctionInvocationBinding,
    current_function_invocation,
    function_invocation_scope,
)


@pytest.mark.asyncio
async def test_function_context_is_isolated_across_concurrent_tasks() -> None:
    first_pod_id = uuid7()
    second_pod_id = uuid7()

    async def resolve(pod_id, token):
        binding = FunctionInvocationBinding(
            base_url="https://api.example.test",
            token=token,
            pod_id=pod_id,
        )
        with function_invocation_scope(binding):
            await asyncio.sleep(0)
            context = FunctionContext(
                pod_id=pod_id,
                function_id=str(uuid7()),
                user_id=uuid7(),
            )
            pod = context.pod
            legacy_pod = await asyncio.to_thread(Pod.from_env)
            active = current_function_invocation()
            assert active is binding
            return pod.pod_id, legacy_pod.pod_id

    first, second = await asyncio.gather(
        resolve(first_pod_id, "first-token"),
        resolve(second_pod_id, "second-token"),
    )

    assert first == (str(first_pod_id), str(first_pod_id))
    assert second == (str(second_pod_id), str(second_pod_id))
    assert current_function_invocation() is None


def test_function_context_rejects_cross_pod_override() -> None:
    binding = FunctionInvocationBinding(
        base_url="https://api.example.test",
        token="token",
        pod_id=uuid7(),
    )

    with function_invocation_scope(binding):
        try:
            Pod.from_env(pod_id=str(uuid7()))
        except Exception as exc:
            assert "cannot override" in str(exc)
        else:
            raise AssertionError("cross-pod override must fail")
