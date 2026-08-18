"""Admitting one agent run onto an Agent Host.

Everything that can refuse a run happens here and only here: the host must
exist and not be revoked, its harness must be ready and unchanged since the
profile was validated, and the selections must still be legal against the
harness's live configuration. Once this returns, the run has a lease and the
rest of the dispatch machinery only ever moves it forward.

A plain function over the unit of work, following ``agent_host_recovery``: one
caller, so a class would add indirection without adding polymorphism.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.agent_host import (
    AgentHostCommandKind,
    AgentHostCommandState,
    AgentHostHarnessHealth,
    AgentHostRunSpec,
    AgentHostRunState,
)
from app.modules.agent.domain.agent_host_selections import (
    validate_agent_host_selections,
)
from app.modules.agent.infrastructure.agent_host_repository import (
    AgentHostRepository,
)
from app.modules.agent.infrastructure.agent_host_repository_common import (
    DEFAULT_COMMAND_TTL_SECONDS,
    AgentHostNotFound,
    AgentHostRunConflict,
    utcnow,
)
from app.modules.agent.infrastructure.runtime_models import (
    AgentHostCommandModel,
    AgentHostRunLeaseModel,
)


async def enqueue_run(
    uow: SqlAlchemyUnitOfWork,
    *,
    host_id: UUID,
    harness_id: UUID,
    runtime_profile_id: UUID,
    run_spec: AgentHostRunSpec,
    encrypted_mcp_payload: dict,
    now: datetime | None = None,
    command_ttl_seconds: int = DEFAULT_COMMAND_TTL_SECONDS,
) -> AgentHostCommandModel:
    """Create this run's lease and its START_RUN command, exactly once.

    The lease is keyed by ``run_id``, so a redispatch of the same run finds the
    existing lease and returns its command rather than creating a second one:
    double-dispatch is structurally impossible, not merely guarded.
    """
    session = uow.session
    timestamp = now or utcnow()
    existing_lease = await session.get(
        AgentHostRunLeaseModel,
        run_spec.agent_run_id,
        with_for_update=True,
    )
    if existing_lease is not None:
        return await _existing_dispatch(
            session,
            existing_lease=existing_lease,
            host_id=host_id,
            harness_id=harness_id,
            runtime_profile_id=runtime_profile_id,
            run_spec=run_spec,
        )

    await _require_admissible_harness(
        uow,
        host_id=host_id,
        harness_id=harness_id,
        run_spec=run_spec,
    )

    lease = AgentHostRunLeaseModel(
        run_id=run_spec.agent_run_id,
        host_id=host_id,
        harness_id=harness_id,
        runtime_profile_id=runtime_profile_id,
        lease_epoch=1,
        state=AgentHostRunState.QUEUED_FOR_HOST.value,
        accepted_at=None,
        lease_expires_at=timestamp + timedelta(seconds=command_ttl_seconds),
        created_at=timestamp,
        updated_at=timestamp,
    )
    payload = run_spec.model_dump(mode="json")
    # The MCP configuration carries run-scoped credentials, so it rests
    # encrypted inside the command and is decrypted only when the command is
    # delivered to the host.
    payload["encrypted_mcp"] = encrypted_mcp_payload
    command = AgentHostCommandModel(
        host_id=host_id,
        run_id=run_spec.agent_run_id,
        kind=AgentHostCommandKind.START_RUN.value,
        lease_epoch=lease.lease_epoch,
        payload=payload,
        state=AgentHostCommandState.QUEUED.value,
        expires_at=timestamp + timedelta(seconds=command_ttl_seconds),
    )
    session.add_all([lease, command])
    await session.flush()
    return command


async def _existing_dispatch(
    session,
    *,
    existing_lease: AgentHostRunLeaseModel,
    host_id: UUID,
    harness_id: UUID,
    runtime_profile_id: UUID,
    run_spec: AgentHostRunSpec,
) -> AgentHostCommandModel:
    """Return this run's existing START_RUN, or refuse a conflicting one."""
    existing = (
        await session.execute(
            select(AgentHostCommandModel)
            .where(
                AgentHostCommandModel.run_id == run_spec.agent_run_id,
                AgentHostCommandModel.kind == AgentHostCommandKind.START_RUN.value,
                AgentHostCommandModel.lease_epoch == existing_lease.lease_epoch,
            )
            .order_by(AgentHostCommandModel.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if (
        existing is None
        or existing.host_id != host_id
        or existing_lease.harness_id != harness_id
        or existing_lease.runtime_profile_id != runtime_profile_id
    ):
        raise AgentHostRunConflict(
            "agent run already has a different Agent Host dispatch"
        )
    return existing


async def _require_admissible_harness(
    uow: SqlAlchemyUnitOfWork,
    *,
    host_id: UUID,
    harness_id: UUID,
    run_spec: AgentHostRunSpec,
) -> None:
    host_repository = AgentHostRepository(uow)
    host = await host_repository.require(host_id, for_update=True)
    if host.revoked_at is not None:
        raise AgentHostRunConflict("Agent Host is revoked")
    harness = await host_repository.get_harness(harness_id=harness_id)
    if harness is None or harness.host_id != host_id:
        raise AgentHostNotFound("Agent Host harness was not found")
    if harness.health != AgentHostHarnessHealth.READY.value:
        raise AgentHostRunConflict(f"harness is not ready: {harness.health}")
    if harness.config_revision != run_spec.profile_revision:
        raise AgentHostRunConflict(
            "harness configuration changed after profile validation"
        )
    try:
        validate_agent_host_selections(
            config_options=harness.config_options or [],
            selections=run_spec.config_selections,
        )
    except ValueError as exc:
        raise AgentHostRunConflict(str(exc)) from exc
