"""Execution context passed into agent harnesses."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.modules.agent.domain.value_objects import JsonObject


class AgentContext(BaseModel):
    """Request context exposed to tools and framework deps."""

    user_id: UUID
    org_id: UUID | None = None
    pod_id: UUID
    conversation_id: UUID
    agent_name: str | None = None
    agent_run_id: UUID | None = None
    metadata: JsonObject | None = None
    # Whether this run is the pod's own assistant rather than a named agent.
    # Every builder of this context resolves it from the agent it already holds
    # (`AgentKind.POD_DEFAULT`, or `is_pod_default_agent` where only ids are in
    # hand), so nothing downstream has to re-derive it from an id or a name.
    #
    # Two things read it. The LEMMA capability assembler keeps POD/SUBAGENTS
    # deferred behind ToolSearch for the assistant, because it accumulates every
    # optional toolset, while a named agent chose its own and gets them injected
    # directly. And the tool-side authorization contexts delegate as the
    # invoking user for the assistant, where a named agent is limited to its own
    # resource grants.
    is_pod_default_agent: bool = False
    # Whether this run gets the memory contract and its AGENTS.md scopes. Not
    # simply "MEMORY is on the agent": memory carries no tools, so it is inert
    # without WORKSPACE_CLI or POD to read and write with -- see
    # `memory_is_active`, which the run-context builder resolves once here so
    # the capability assembler and the brief builder cannot disagree about it.
    memory_enabled: bool = False
    # This agent's grant summary, loaded once for the run. Toolset selection
    # derives POD and CONNECTORS from it, and the tool assembler reuses it
    # rather than reading the same rows a second time. Typed loosely here to
    # keep this domain model free of a tools-layer import.
    grant_summary: object | None = None
    # Rendered runtime brief (pod/user/granted resources) appended to the system
    # prompt. Built once per run by the runner; harness-neutral so it just rides
    # along on the context.
    context_brief: str | None = None

    model_config = ConfigDict(arbitrary_types_allowed=True)
