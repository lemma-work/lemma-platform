"""The tool-context fields every variant of a run's context shares.

``RunSession._identity`` fills it once for the turn, the resume and the Agent
Host relay, so the three cannot drift (see ``services/run_session``).
"""

from __future__ import annotations

from typing import TypedDict
from uuid import UUID

from app.modules.agent.domain.project_repo import ProjectRepo
from app.modules.agent.domain.value_objects import JsonObject


class RunIdentity(TypedDict):
    """The tool-context fields every variant of a run's context shares."""

    user_id: UUID
    org_id: UUID | None
    pod_id: UUID
    conversation_id: UUID
    agent_name: str | None
    agent_run_id: UUID | None
    workload_type: str | None
    workload_id: UUID | None
    is_pod_default_agent: bool
    runtime_profile: JsonObject | None
    workspace_id: str
    workspace_cwd: str
    machine: str | None
    started_in_lemma: bool
    workspace_repo: ProjectRepo | None
    pod_cwd: str


__all__ = ["RunIdentity"]
