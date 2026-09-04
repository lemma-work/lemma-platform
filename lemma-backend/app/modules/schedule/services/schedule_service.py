from typing import List, Optional
from uuid import UUID, uuid4

from app.core.authorization.context import (
    Context,
    ResourceRef,
    ResourceType,
    ResourceVisibility,
)
from app.core.authorization.permissions import Permissions
from app.core.helpers.slug import normalize_resource_name
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.schedule.domain.errors import (
    ScheduleInfrastructureError,
    ScheduleValidationError,
)
from app.modules.schedule.domain.interfaces import (
    DatastoreSchedulePolicy,
    ExternalScheduleWriter,
    ScheduleRepository,
    ScheduleTargetResolver,
)
from app.modules.schedule.domain.schedule import (
    DatastoreScheduleConfig,
    ScheduleCreateEntity,
    ScheduleEntity,
    ScheduleType,
    ScheduleUpdateEntity,
    normalize_datastore_schedule_config,
)
from app.modules.schedule.contracts.webhook_source import WebhookSourceRegistry
from app.modules.schedule.repositories.schedule_repository import (
    ScheduleRepository as ScheduleRepositoryImpl,
)
from app.modules.schedule.services.time_schedule_policy import (
    validate_time_schedule_config,
)
from app.modules.schedule.services.schedule_run_service import ScheduleRunService
from app.modules.schedule.services.schedule_target_policy import (
    agent_execute_ref,
    normalize_agent_selector,
    target_agent_after_update,
    derive_webhook_target_from_workflow_start,
    named_agent_target_fields,
    validate_global_workflow_is_unclaimed,
    validate_instruction_survives_update,
    validate_target_instruction,
    validate_single_target,
    workflow_target_fields,
)
from app.modules.schedule.services.schedule_update_policy import (
    is_explicit_reactivation,
    validate_schedule_update_policies,
)
from app.modules.schedule.services.webhook_source_policy import validate_webhook_source
from app.core.log.log import get_logger

logger = get_logger(__name__)


class ScheduleService:
    """Service for managing schedules."""

    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        schedule_repository: Optional[ScheduleRepository] = None,
        external_schedule_writer: Optional[ExternalScheduleWriter] = None,
        target_resolver: ScheduleTargetResolver | None = None,
        datastore_policy: DatastoreSchedulePolicy | None = None,
        authorization_service: object | None = None,
        webhook_sources: WebhookSourceRegistry | None = None,
    ):
        self.uow = uow
        self.schedule_repository = schedule_repository or ScheduleRepositoryImpl(
            uow=uow
        )
        if external_schedule_writer is None:
            from app.modules.schedule.infrastructure.adapters.external_schedule_writer import (
                ExternalScheduleWriterAdapter,
            )

            external_schedule_writer = ExternalScheduleWriterAdapter(uow)
        self.external_schedule_writer = external_schedule_writer
        self.authorization_service = authorization_service
        # Injected rather than resolved here: the registry is a deployment
        # fact assembled at the composition root, which a module may not import.
        self.webhook_sources = webhook_sources
        if datastore_policy is None:
            from app.modules.schedule.infrastructure.adapters.datastore_table_policy import (
                DatastoreTableSchedulePolicy,
            )

            datastore_policy = DatastoreTableSchedulePolicy(uow)
        self.datastore_policy = datastore_policy
        self.run_service = ScheduleRunService(
            uow=uow,
            schedule_repository=self.schedule_repository,
            datastore_policy=datastore_policy,
        )
        if target_resolver is None:
            from app.modules.schedule.infrastructure.adapters.target_resolver import (
                SqlAlchemyScheduleTargetResolver,
            )

            target_resolver = SqlAlchemyScheduleTargetResolver(uow)
        self.target_resolver = target_resolver

    async def list_schedule_runs(
        self,
        *,
        pod_id: UUID,
        schedule_id: UUID,
        ctx: Context,
        limit: int,
    ):
        return await self.run_service.list_schedule_runs(
            pod_id=pod_id, schedule_id=schedule_id, ctx=ctx, limit=limit
        )

    async def retry_schedule_run(
        self,
        *,
        pod_id: UUID,
        schedule_id: UUID,
        run_id: UUID,
        ctx: Context,
    ):
        return await self.run_service.retry_schedule_run(
            pod_id=pod_id, schedule_id=schedule_id, run_id=run_id, ctx=ctx
        )

    async def create_schedule(
        self,
        schedule_create: ScheduleCreateEntity,
        ctx: Context | None = None,
    ) -> ScheduleEntity:
        """Create a new schedule and schedule/provider-create side effects."""

        schedule_create = schedule_create.model_copy(
            update={
                "name": self._normalize_or_generate_schedule_name(schedule_create),
            }
        )
        schedule_create = await self._resolve_create_target(schedule_create)
        schedule_create = schedule_create.model_copy(
            update={
                "visibility": await self._resolve_create_visibility(schedule_create)
            }
        )
        await self._validate_name_available(schedule_create)
        await self._validate_target(schedule_create)
        await self._require_target_execute(schedule_create, ctx=ctx)
        await self._require_datastore_table_update(schedule_create, ctx=ctx)
        if schedule_create.schedule_type == ScheduleType.TIME:
            validate_time_schedule_config(schedule_create.config)
        elif schedule_create.schedule_type == ScheduleType.WEBHOOK:
            validate_webhook_source(schedule_create, self.webhook_sources)
        schedule = ScheduleEntity(**schedule_create.model_dump())
        created = await self.schedule_repository.create(schedule)

        if (
            created.schedule_type == ScheduleType.WEBHOOK
            and created.connector_trigger_id
            and created.account_id
        ):
            try:
                provisioned = (
                    await self.external_schedule_writer.create_provider_trigger(created)
                )
                # A source needing no subscription still supplies a routing key.
                if provisioned.apply_to(created.config):
                    updated = await self.schedule_repository.update(
                        created.id,
                        config=created.config,
                    )
                    if updated:
                        created = updated
            except Exception as exc:
                logger.debug(
                    "schedule.schedule_service.create_external_schedule_s.propagated",
                    exc_info=True,
                )
                await self.schedule_repository.delete(created.id)
                raise ScheduleValidationError(
                    f"Failed to create external schedule: {exc}"
                ) from exc

        return created

    async def _resolve_create_target(
        self, schedule_create: ScheduleCreateEntity
    ) -> ScheduleCreateEntity:
        update_data: dict[str, object] = {}
        if schedule_create.workflow_name:
            if schedule_create.pod_id is None:
                raise ScheduleValidationError(
                    "pod_id is required for workflow schedules"
                )
            workflow = await self._get_workflow_by_name(
                pod_id=schedule_create.pod_id,
                workflow_name=schedule_create.workflow_name,
            )
            update_data.update(workflow_target_fields(workflow.id))
            if schedule_create.schedule_type == ScheduleType.WEBHOOK:
                update_data.update(
                    derive_webhook_target_from_workflow_start(
                        workflow,
                        config=schedule_create.config,
                        requested_connector_trigger_id=(
                            schedule_create.connector_trigger_id
                        ),
                    )
                )
        if schedule_create.agent_name:
            if schedule_create.pod_id is None:
                raise ScheduleValidationError("pod_id is required for agent schedules")
            update_data.update(
                await self._agent_target_fields(
                    pod_id=schedule_create.pod_id,
                    agent_name=schedule_create.agent_name,
                    instruction=schedule_create.instruction,
                )
            )
        return schedule_create.model_copy(update=update_data)

    async def _agent_target_fields(
        self, *, pod_id: UUID, agent_name: str, instruction: str | None
    ) -> dict[str, object]:
        """The target columns for whichever agent this name means.

        `POD_DEFAULT` is a wire selector rather than a name, so it is normalised
        to the row's own before the lookup -- after which the pod's assistant is
        found by exactly the query every other agent is found by.

        The instruction rule is checked here because this is where the resolved
        agent is in hand: the question is whether *it* has a standing
        instruction, which a name alone cannot answer.
        """
        agent = await self._get_agent_by_name(
            pod_id=pod_id, agent_name=normalize_agent_selector(agent_name)
        )
        validate_target_instruction(agent, instruction)
        return named_agent_target_fields(agent.id)

    async def _resolve_update_target(
        self,
        existing: ScheduleEntity,
        schedule_update: ScheduleUpdateEntity,
    ) -> dict:
        update_data = schedule_update.model_dump(exclude_none=True)
        if existing.schedule_type == ScheduleType.DATASTORE and "config" in update_data:
            try:
                update_data["config"] = normalize_datastore_schedule_config(
                    update_data["config"]
                )
            except ValueError as exc:
                raise ScheduleValidationError(str(exc)) from exc
        if "name" in update_data:
            update_data["name"] = normalize_resource_name(str(update_data["name"]))
        if "visibility" in update_data:
            update_data["visibility"] = self._normalize_schedule_visibility(
                update_data["visibility"]
            )
        if schedule_update.workflow_name:
            if existing.pod_id is None:
                raise ScheduleValidationError(
                    "pod_id is required for workflow schedules"
                )
            workflow = await self._get_workflow_by_name(
                pod_id=existing.pod_id,
                workflow_name=schedule_update.workflow_name,
            )
            update_data.update(workflow_target_fields(workflow.id))
            if existing.schedule_type == ScheduleType.WEBHOOK:
                update_data.update(
                    derive_webhook_target_from_workflow_start(
                        workflow,
                        config=update_data.get("config", existing.config),
                        requested_connector_trigger_id=None,
                    )
                )
        if schedule_update.agent_name:
            if existing.pod_id is None:
                raise ScheduleValidationError("pod_id is required for agent schedules")
            update_data.update(
                await self._agent_target_fields(
                    pod_id=existing.pod_id,
                    agent_name=schedule_update.agent_name,
                    instruction=(
                        schedule_update.instruction
                        if schedule_update.instruction is not None
                        else existing.instruction
                    ),
                )
            )
        if existing.schedule_type == ScheduleType.WEBHOOK and schedule_update.config:
            # Only a config the caller wrote; a re-derived one is the row's own.
            candidate = existing.model_copy(update={"config": update_data["config"]})
            validate_webhook_source(candidate, self.webhook_sources)
        # Whatever the schedule points at once this lands -- the retargeted
        # agent if it is being retargeted, otherwise the one it already had.
        target_agent = await target_agent_after_update(
            self.target_resolver, existing, update_data
        )
        validate_instruction_survives_update(target_agent, schedule_update)
        update_data.pop("workflow_name", None)
        update_data.pop("agent_name", None)
        return update_data

    @staticmethod
    def _normalize_schedule_visibility(value: ResourceVisibility | str | None) -> str:
        if value is None:
            return ResourceVisibility.POD.value
        raw = value.value if isinstance(value, ResourceVisibility) else str(value)
        try:
            return ResourceVisibility(raw.upper()).value
        except ValueError as exc:
            raise ScheduleValidationError(f"Invalid visibility: {value}") from exc

    async def _resolve_create_visibility(
        self, schedule_create: ScheduleCreateEntity
    ) -> str:
        """Resolve the visibility a new schedule is stored with.

        An explicit visibility is always honored. DATASTORE schedules and
        GLOBAL-workflow schedules default to POD; other schedules default to
        PERSONAL.
        """
        if schedule_create.visibility is not None:
            return self._normalize_schedule_visibility(schedule_create.visibility)
        if schedule_create.schedule_type == ScheduleType.DATASTORE:
            return ResourceVisibility.POD.value
        if await self._targets_global_workflow(schedule_create):
            return ResourceVisibility.POD.value
        return ResourceVisibility.PERSONAL.value

    async def _targets_global_workflow(
        self, schedule_create: ScheduleCreateEntity
    ) -> bool:
        if schedule_create.workflow_id is None:
            return False
        workflow = await self.target_resolver.get_workflow(schedule_create.workflow_id)
        return workflow is not None and workflow.is_global_workflow

    async def _get_workflow_by_name(self, *, pod_id: UUID, workflow_name: str):
        workflow = await self.target_resolver.get_workflow_by_name(
            pod_id,
            normalize_resource_name(workflow_name),
        )
        if workflow is None:
            raise ScheduleValidationError("Workflow target not found in pod")
        return workflow

    async def _get_agent_by_name(self, *, pod_id: UUID, agent_name: str):
        agent = await self.target_resolver.get_agent_by_name(
            pod_id,
            agent_name.strip(),
        )
        if agent is None:
            raise ScheduleValidationError("Agent target not found in pod")
        return agent

    async def _validate_target(self, schedule_create: ScheduleCreateEntity) -> None:
        if not validate_single_target(schedule_create):
            return

        if schedule_create.pod_id is None:
            raise ScheduleValidationError("pod_id is required for target schedules")

        if schedule_create.workflow_id is not None:
            flow = await self.target_resolver.get_workflow(schedule_create.workflow_id)
            if flow is None or flow.pod_id != schedule_create.pod_id:
                raise ScheduleValidationError("Workflow target not found in pod")
            if flow.is_global_workflow:
                validate_global_workflow_is_unclaimed(
                    await self.schedule_repository.find_active_by_workflow(
                        pod_id=schedule_create.pod_id,
                        workflow_id=flow.id,
                        user_id=schedule_create.user_id,
                    )
                )

        if schedule_create.agent_id is not None:
            agent = await self.target_resolver.get_agent(schedule_create.agent_id)
            if agent is None or agent.pod_id != schedule_create.pod_id:
                raise ScheduleValidationError("Agent target not found in pod")

    def _normalize_or_generate_schedule_name(
        self, schedule_create: ScheduleCreateEntity
    ) -> str | None:
        if schedule_create.is_internal or schedule_create.pod_id is None:
            return (
                normalize_resource_name(schedule_create.name)
                if schedule_create.name
                else schedule_create.name
            )
        if schedule_create.name:
            return normalize_resource_name(schedule_create.name)
        target_name = (
            schedule_create.workflow_name
            or schedule_create.agent_name
            or schedule_create.schedule_type.value.lower()
        )
        base = normalize_resource_name(
            f"{target_name}_{schedule_create.schedule_type.value.lower()}_schedule"
        )
        return f"{base}_{uuid4().hex[:8]}"

    async def _validate_name_available(
        self,
        schedule_create: ScheduleCreateEntity,
        *,
        existing_schedule_id: UUID | None = None,
    ) -> None:
        if not schedule_create.name or schedule_create.pod_id is None:
            return
        existing = await self.schedule_repository.get_by_name(
            pod_id=schedule_create.pod_id,
            name=schedule_create.name,
        )
        if existing and existing.id != existing_schedule_id:
            raise ScheduleValidationError(
                f"Schedule already exists in pod with name: {schedule_create.name}"
            )

    async def update_schedule(
        self,
        schedule_id: UUID,
        schedule_update: ScheduleUpdateEntity,
        ctx: Context | None = None,
    ) -> Optional[ScheduleEntity]:
        """Update a schedule and update scheduler state if needed."""
        existing = await self.schedule_repository.get(schedule_id)
        if not existing:
            return None

        update_data = await self._resolve_update_target(existing, schedule_update)
        await validate_schedule_update_policies(
            existing,
            update_data,
            ctx=ctx,
            require_datastore_update=self._require_datastore_table_update,
        )
        if "name" in update_data and update_data["name"]:
            await self._validate_name_available(
                existing.model_copy(update={"name": update_data["name"]}),
                existing_schedule_id=schedule_id,
            )
        if "workflow_id" in update_data or "agent_id" in update_data:
            candidate = existing.model_copy(update=update_data)
            await self._require_target_execute(candidate, ctx=ctx)
        if "workflow_id" in update_data and update_data["workflow_id"] is not None:
            workflow = await self.target_resolver.get_workflow(
                update_data["workflow_id"]
            )
            if workflow and workflow.is_global_workflow:
                existing_for_workflow = (
                    await self.schedule_repository.find_active_by_workflow(
                        pod_id=existing.pod_id,
                        workflow_id=update_data["workflow_id"],
                        user_id=existing.user_id,
                    )
                )
                validate_global_workflow_is_unclaimed(
                    [item for item in existing_for_workflow if item.id != schedule_id]
                )
        updated = await self.schedule_repository.update(schedule_id, **update_data)

        if is_explicit_reactivation(existing, updated, update_data):
            # Reactivating a schedule clears its circuit-breaker failure streak so
            # a re-enabled schedule starts fresh instead of tripping again on the
            # next failure.
            await self.schedule_repository.reset_consecutive_failures(schedule_id)

        return updated

    async def delete_schedule(self, schedule_id: UUID) -> bool:
        """Delete a schedule and remove external/scheduler side effects."""
        existing = await self.schedule_repository.get(schedule_id)
        if not existing:
            return False

        if (
            existing.schedule_type == ScheduleType.WEBHOOK
            and existing.connector_trigger_id
            and existing.account_id
            and existing.config.get("provider_trigger_id")
        ):
            try:
                await self.external_schedule_writer.delete_provider_trigger(existing)
            except ScheduleInfrastructureError:
                raise
            except Exception as exc:
                logger.debug(
                    "schedule.schedule_service.delete_external_schedule_s.propagated",
                    schedule_id=schedule_id,
                    exc_info=True,
                )
                raise ScheduleInfrastructureError(
                    f"Failed to delete external schedule for {schedule_id}: {exc}"
                ) from exc

        return await self.schedule_repository.delete(schedule_id)

    async def delete_all_for_pod(self, pod_id: UUID) -> int:
        """Delete every schedule in a pod with full teardown (cleanup-only).

        System-level: no RBAC filtering, includes internal schedules.
        Best-effort in one specific respect: an *external* teardown failure
        (APScheduler/Composio) does not abort the rest, and the row is
        force-deleted anyway so the schedule can no longer fire. A database
        failure is not best-effort and propagates -- swallowing it left the
        session rollback-pending and the caller's commit failing later with an
        error naming none of this, under a pod reported cleaned up.
        """
        schedules = await self.schedule_repository.list_all_by_pod(pod_id)
        deleted = 0
        for schedule in schedules:
            try:
                if await self.delete_schedule(schedule.id):
                    deleted += 1
            except ScheduleInfrastructureError:
                # ``delete_schedule`` wraps every external failure in this, so
                # this arm is the external teardown and nothing else.
                logger.debug(
                    "schedule.cleanup.primary_failed",
                    pod_id=pod_id,
                    exc_info=True,
                )
                if await self.schedule_repository.delete(schedule.id):
                    deleted += 1
        return deleted

    async def list_schedules(
        self,
        schedule_type: Optional[ScheduleType] = None,
        is_active: Optional[bool] = None,
        pod_id: Optional[UUID] = None,
        user_id: Optional[UUID] = None,
        agent_name: str | None = None,
        workflow_name: str | None = None,
        name: str | None = None,
        limit: int = 100,
        cursor: UUID | None = None,
        ctx: Context | None = None,
    ) -> tuple[List[ScheduleEntity], UUID | None]:
        """List schedules."""
        agent_id = None
        workflow_id = None
        if agent_name and workflow_name:
            raise ScheduleValidationError(
                "Only one of agent_name or workflow_name can be provided"
            )
        if agent_name:
            if pod_id is None:
                raise ScheduleValidationError("pod_id is required for agent schedules")
            agent_id = (
                await self._get_agent_by_name(
                    pod_id=pod_id, agent_name=normalize_agent_selector(agent_name)
                )
            ).id
        if workflow_name:
            if pod_id is None:
                raise ScheduleValidationError(
                    "pod_id is required for workflow schedules"
                )
            workflow_id = (
                await self._get_workflow_by_name(
                    pod_id=pod_id,
                    workflow_name=workflow_name,
                )
            ).id
        normalized_name = normalize_resource_name(name) if name else None
        if ctx is None:
            raise RuntimeError("Context is required for schedule listing")

        return await self.schedule_repository.list(
            schedule_type=schedule_type,
            is_active=is_active,
            pod_id=pod_id,
            user_id=user_id,
            agent_id=agent_id,
            workflow_id=workflow_id,
            name=normalized_name,
            ctx=ctx,
            limit=limit,
            cursor=cursor,
        )

    async def get_schedule(
        self,
        schedule_id: UUID,
        ctx: Context | None = None,
    ) -> Optional[ScheduleEntity]:
        """Get schedule by ID."""
        return await self.schedule_repository.get(schedule_id, ctx=ctx)

    async def _require_target_execute(
        self,
        schedule: ScheduleCreateEntity | ScheduleEntity,
        ctx: Context | None = None,
    ) -> None:
        if schedule.pod_id is None:
            return
        if ctx is None:
            raise RuntimeError("Context is required for schedule target authorization")
        if schedule.agent_id is not None:
            # The assistant is asked about pod-scoped, the same as everywhere
            # else it is asked about. Its row's id *is* the pod's, so both arms
            # would name the same thing -- but the resource type is not
            # cosmetic, and an AGENT-typed check would newly hit the
            # resource-owner shortcut for whoever created the pod.
            await ctx.require(
                Permissions.AGENT_EXECUTE,
                agent_execute_ref(schedule.agent_id, pod_id=schedule.pod_id),
            )
        if schedule.workflow_id is not None:
            await ctx.require(
                Permissions.WORKFLOW_EXECUTE,
                ResourceRef(
                    resource_type=ResourceType.WORKFLOW,
                    resource_id=schedule.workflow_id,
                    pod_id=schedule.pod_id,
                ),
            )

    async def _require_datastore_table_update(
        self,
        schedule: ScheduleCreateEntity | ScheduleEntity,
        ctx: Context | None,
    ) -> None:
        if schedule.schedule_type != ScheduleType.DATASTORE:
            return
        if schedule.pod_id is None or ctx is None:
            raise RuntimeError(
                "Context and pod_id are required for DATASTORE schedule authorization"
            )
        try:
            config = DatastoreScheduleConfig(**schedule.config)
        except ValueError as exc:
            raise ScheduleValidationError(
                "DATASTORE schedules must declare an explicit table_name."
            ) from exc
        await self.datastore_policy.require_table_update(
            pod_id=schedule.pod_id,
            table_name=config.table_name,
            ctx=ctx,
        )
