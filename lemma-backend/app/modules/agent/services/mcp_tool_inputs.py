"""What every Agent Host tool call re-derived, kept for the run's lease.

A coding agent makes hundreds of Lemma tool calls a turn, and each one resolved
the run's runtime profile and read the agent's grants again to build the same
toolsets. Neither changes inside a lease often enough to be worth that: the
runtime is fixed when the run starts, and grants change when a person edits
them. So both are kept in Redis under the run and its lease epoch, for a short
while, and thrown away early when the agent's grants change
(``core/authorization/grant_generation``).

The runtime is kept without its provider ``config``: the bridge builds no model
client -- it holds no credentials to build one with -- and reads only what the
runtime can do, while the config may carry a provider's custom headers, which
have no business sitting in a cache.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ValidationError
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.core.log.log import get_logger
from app.modules.agent.tools.toolset_selection import AgentGrantSummary

logger = get_logger(__name__)

#: Long enough to cover a burst of calls, short enough that a profile edited
#: mid-run is seen within a minute.
MCP_INPUTS_TTL_SECONDS = 60
#: The grant token of a run with no agent of its own: the pod's assistant,
#: which holds no grants, so there is nothing to change.
NO_AGENT_GENERATION = "no-agent"


class CachedGrantSummary(BaseModel):
    function_ids: list[str]
    agent_ids: list[str]
    has_pod_data: bool
    has_connectors: bool

    @classmethod
    def of(cls, summary: AgentGrantSummary) -> CachedGrantSummary:
        return cls(
            function_ids=list(summary.function_ids),
            agent_ids=list(summary.agent_ids),
            has_pod_data=summary.has_pod_data,
            has_connectors=summary.has_connectors,
        )

    def summary(self) -> AgentGrantSummary:
        return AgentGrantSummary(
            function_ids=tuple(self.function_ids),
            agent_ids=tuple(self.agent_ids),
            has_pod_data=self.has_pod_data,
            has_connectors=self.has_connectors,
        )


class McpToolInputs(BaseModel):
    """The derived inputs, and the grant token they were derived under."""

    grant_generation: str
    runtime_profile: dict[str, object] | None
    grants: CachedGrantSummary


def without_provider_config(
    runtime_profile: dict[str, object] | None,
) -> dict[str, object] | None:
    if runtime_profile is None:
        return None
    return {key: value for key, value in runtime_profile.items() if key != "config"}


class JsonStore(Protocol):
    """The part of ``RedisJsonCache`` this uses."""

    async def get_json(self, suffix: str) -> object | None: ...

    async def set_json(self, suffix: str, value: object) -> None: ...


class McpToolInputsCache:
    def __init__(self, cache: JsonStore) -> None:
        self._cache = cache

    async def get(
        self, *, run_id: UUID, lease_epoch: int, grant_generation: str
    ) -> McpToolInputs | None:
        """The inputs kept for this lease, unless the agent's grants moved on."""
        try:
            raw = await self._cache.get_json(_suffix(run_id, lease_epoch))
        except RedisError, OSError, TimeoutError:
            logger.warning(
                "agent.mcp_tool_inputs.read_failed.degraded",
                agent_run_id=str(run_id),
                exc_info=True,
            )
            return None
        if raw is None:
            return None
        try:
            inputs = McpToolInputs.model_validate(raw)
        except ValidationError:
            # Written by a release with another shape: derive afresh.
            return None
        return inputs if inputs.grant_generation == grant_generation else None

    async def put(
        self, *, run_id: UUID, lease_epoch: int, inputs: McpToolInputs
    ) -> None:
        try:
            await self._cache.set_json(
                _suffix(run_id, lease_epoch), inputs.model_dump(mode="json")
            )
        except RedisError, OSError, TimeoutError:
            logger.warning(
                "agent.mcp_tool_inputs.write_failed.degraded",
                agent_run_id=str(run_id),
                exc_info=True,
            )


def _suffix(run_id: UUID, lease_epoch: int) -> str:
    return f"{run_id}:{lease_epoch}"


@lru_cache(maxsize=1)
def mcp_tool_inputs_cache() -> McpToolInputsCache:
    return McpToolInputsCache(
        RedisJsonCache(
            redis_url=settings.redis_url,
            key_prefix="agent:mcp-inputs",
            ttl_seconds=MCP_INPUTS_TTL_SECONDS,
        )
    )
