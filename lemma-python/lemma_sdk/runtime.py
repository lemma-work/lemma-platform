from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, PrivateAttr


@dataclass(slots=True)
class FunctionInvocationBinding:
    base_url: str
    token: str
    pod_id: UUID
    organization_id: UUID | None = None
    run_id: UUID | None = None
    _resources: list[Any] = field(default_factory=list, repr=False)

    def register(self, resource: Any) -> Any:
        self._resources.append(resource)
        return resource

    def close(self) -> None:
        while self._resources:
            resource = self._resources.pop()
            close = getattr(resource, "close", None)
            if close is not None:
                close()


_FUNCTION_INVOCATION: ContextVar[FunctionInvocationBinding | None] = ContextVar(
    "lemma_function_invocation",
    default=None,
)


def current_function_invocation() -> FunctionInvocationBinding | None:
    return _FUNCTION_INVOCATION.get()


@contextmanager
def function_invocation_scope(binding: FunctionInvocationBinding):
    token: Token[FunctionInvocationBinding | None] = _FUNCTION_INVOCATION.set(binding)
    try:
        yield binding
    finally:
        try:
            binding.close()
        finally:
            _FUNCTION_INVOCATION.reset(token)


class FunctionContext(BaseModel):
    """Runtime context passed to Lemma functions."""

    pod_id: UUID
    function_id: str
    user_id: UUID
    user_email: str | None = None
    config: Any = None
    model_config = ConfigDict(arbitrary_types_allowed=True)
    _pod: Any = PrivateAttr(default=None)

    @property
    def pod(self) -> Any:
        if self._pod is not None:
            return self._pod
        binding = current_function_invocation()
        if binding is None:
            raise RuntimeError("ctx.pod is available only during function execution")
        if binding.pod_id != self.pod_id:
            raise RuntimeError("function invocation pod does not match its context")
        from .pod import Pod

        self._pod = binding.register(
            Pod(
                pod_id=str(binding.pod_id),
                org_id=(
                    str(binding.organization_id)
                    if binding.organization_id is not None
                    else None
                ),
                base_url=binding.base_url,
                token=binding.token,
            )
        )
        return self._pod


__all__ = [
    "FunctionContext",
    "FunctionInvocationBinding",
    "current_function_invocation",
    "function_invocation_scope",
]
