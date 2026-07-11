from typing import List, Optional
from uuid import UUID, uuid4

from app.core.authorization.context import Context, ResourceRef, ResourceType, ResourceVisibility
from app.core.authorization.permissions import Permissions
from app.core.helpers.slug import normalize_resource_name
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.schedule.domain.errors import (
    ScheduleInfrastructureError,
    ScheduleValidationError,
)
from app.modules.schedule.domain.interfaces import (
    ExternalScheduleWriter,
    ScheduleRepository,
    SchedulerService,
    ScheduleTarget,
    ScheduleTargetResolver,
)
from app.modules.schedule.domain.schedule import (
    ScheduleCreateEntity,
    ScheduleEntity,
    ScheduleType,
    ScheduleUpdateEntity,
    normalize_datastore_schedule_config,
)
from app.modules.schedule.domain.events.schedule import ScheduleFired
from app.modules.schedule.repositories.schedule_run_repository import (
    ScheduleRunRepository,
)
from app.modules.schedule.repositories.schedule_repository import (
    ScheduleRepository as ScheduleRepositoryImpl,
)
from app.modules.schedule.scheduler.api_client import SchedulerAPIClient
from app.core.log.log import get_logger

logger = get_logger(__name__)


class ScheduleService:
    """Service for managing schedules."""

    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        schedule_repository: Optional[ScheduleRepository] = None,
        scheduler_service: Optional[SchedulerService] = None,
        external_schedule_writer: Optional[ExternalScheduleWriter] = None,
        target_resolver: ScheduleTargetResolver | None = None,
        authorization_service: object | None = None,
    ):
        self.uow = uow
        self.schedule_repository = schedule_repository or ScheduleRepositoryImpl(uow=uow)
        self.scheduler_service = scheduler_service or SchedulerAPIClient()
        if external_schedule_writer is None:
            from app.composition.schedule_connectors import (
                ExternalScheduleWriterAdapter,
            )

            external_schedule_writer = ExternalScheduleWriterAdapter(uow=uow)
        self.external_schedule_writer = external_schedule_writer
        self.authorization_service = authorization_service
        self.run_repository = ScheduleRunRepository(uow)
        if target_resolver is None:
            from app.composition.schedule_targets import (
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
        schedule = await self.get_schedule(schedule_id, ctx=ctx)
        if schedule is None or schedule.pod_id != pod_id:
            return None
        await ctx.require(Permissions.SCHEDULE_READ, ResourceRef.schedule(pod_id, schedule_id))
        return await self.run_repository.list_for_schedule(schedule_id, limit=limit)

    async def retry_schedule_run(
        self,
        *,
        pod_id: UUID,
        schedule_id: UUID,
        run_id: UUID,
        ctx: Context,
    ):
        schedule = await self.get_schedule(schedule_id, ctx=ctx)
        if schedule is None or schedule.pod_id != pod_id:
            return None
        await ctx.require(
            Permissions.SCHEDULE_UPDATE, ResourceRef.schedule(pod_id, schedule_id)
        )
        schedule_run = await self.run_repository.reset_for_retry(
            schedule_id=schedule_id, run_id=run_id
        )
        if schedule_run is None:
            return None
        self.uow.collect_events(
            [
                ScheduleFired(
                    schedule_id=schedule.id,
                    user_id=schedule.user_id,
                    schedule_type=schedule.schedule_type,
                    pod_id=schedule.pod_id,
                    account_id=schedule.account_id,
                    payload=schedule_run.payload,
                    metadata=schedule_run.metadata,
                    llm_output=schedule_run.llm_output,
                    scheduled_at=schedule_run.source_occurred_at,
                    source_event_id=schedule_run.source_event_id,
                    causation_id=schedule_run.id,
                )
            ]
        )
        return schedule_run

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
            update={"visibility": await self._resolve_create_visibility(schedule_create)}
        )
        await self._validate_name_available(schedule_create)
        await self._validate_target(schedule_create)
        await self._require_target_execute(schedule_create, ctx=ctx)
        schedule = ScheduleEntity(**schedule_create.model_dump())
        created = await self.schedule_repository.create(schedule)

        if (
            created.schedule_type == ScheduleType.WEBHOOK
            and created.connector_trigger_id
            and created.account_id
        ):
            try:
                provider_id = (
                    await self.external_schedule_writer.create_provider_trigger(created)
                )
                if provider_id:
                    created.config["provider_trigger_id"] = provider_id
                    updated = await self.schedule_repository.update(
                        created.id,
                        config=created.config,
                    )
                    if updated:
                        created = updated
            except Exception as exc:
                logger.error("Failed to create external schedule: %s", exc)
                await self.schedule_repository.delete(created.id)
                raise ScheduleValidationError(
                    f"Failed to create external schedule: {exc}"
                ) from exc

        if created.schedule_type == ScheduleType.TIME:
            await self.scheduler_service.schedule_job(created)

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
            update_data["workflow_id"] = workflow.id
            update_data["agent_id"] = None
            if schedule_create.schedule_type == ScheduleType.WEBHOOK:
                update_data.update(
                    self._derive_webhook_schedule_from_workflow_start(
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
            agent = await self._get_agent_by_name(
                pod_id=schedule_create.pod_id,
                agent_name=schedule_create.agent_name,
            )
            update_data["agent_id"] = agent.id
            update_data["workflow_id"] = None
        return schedule_create.model_copy(update=update_data)

    def _derive_webhook_schedule_from_workflow_start(
        self,
        workflow: ScheduleTarget,
        *,
        config: dict,
        requested_connector_trigger_id: str | None,
    ) -> dict:
        if workflow.event_trigger_id is None:
            raise ScheduleValidationError(
                "Webhook workflow schedules require an EVENT workflow start"
            )
        if requested_connector_trigger_id:
            raise ScheduleValidationError(
                "connector_trigger_id is only valid for agent webhook schedules; "
                "workflow webhook schedules derive it from the workflow event start"
            )

        config = dict(config or {})
        trigger_config = dict(workflow.event_trigger_config or {})
        conflicting_keys = sorted(
            key
            for key, value in trigger_config.items()
            if key in config and config[key] != value
        )
        if conflicting_keys:
            raise ScheduleValidationError(
                "Schedule config conflicts with workflow event start trigger_config "
                f"for: {', '.join(conflicting_keys)}"
            )
        config.update(trigger_config)

        update_data: dict = {
            "connector_trigger_id": workflow.event_trigger_id,
            "config": config,
        }
        return update_data

    async def _resolve_update_target(
        self,
        existing: ScheduleEntity,
        schedule_update: ScheduleUpdateEntity,
    ) -> dict:
        update_data = schedule_update.model_dump(exclude_none=True)
        if (
            existing.schedule_type == ScheduleType.DATASTORE
            and "config" in update_data
        ):
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
            update_data["workflow_id"] = workflow.id
            update_data["agent_id"] = None
            if existing.schedule_type == ScheduleType.WEBHOOK:
                update_data.update(
                    self._derive_webhook_schedule_from_workflow_start(
                        workflow,
                        config=update_data.get("config", existing.config),
                        requested_connector_trigger_id=None,
                    )
                )
        if schedule_update.agent_name:
            if existing.pod_id is None:
                raise ScheduleValidationError("pod_id is required for agent schedules")
            agent = await self._get_agent_by_name(
                pod_id=existing.pod_id,
                agent_name=schedule_update.agent_name,
            )
            update_data["agent_id"] = agent.id
            update_data["workflow_id"] = None
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

        An explicit visibility is always honored. Otherwise schedules are
        PERSONAL — private to their creator — unless they target a GLOBAL
        workflow, which is a pod-wide singleton whose schedule is a pod-level
        trigger and therefore stays POD-visible to the whole pod.
        """
        if schedule_create.visibility is not None:
            return self._normalize_schedule_visibility(schedule_create.visibility)
        if await self._targets_global_workflow(schedule_create):
            return ResourceVisibility.POD.value
        return ResourceVisibility.PERSONAL.value

    async def _targets_global_workflow(
        self, schedule_create: ScheduleCreateEntity
    ) -> bool:
        if schedule_create.workflow_id is None:
            return False
        workflow = await self.target_resolver.get_workflow(
            schedule_create.workflow_id
        )
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
        if schedule_create.agent_id is None and schedule_create.workflow_id is None:
            return
        if (
            schedule_create.agent_id is not None
            and schedule_create.workflow_id is not None
        ):
            raise ScheduleValidationError(
                "Schedule can target either an agent or workflow, not both"
            )

        if schedule_create.pod_id is None:
            raise ScheduleValidationError("pod_id is required for target schedules")

        if schedule_create.workflow_id is not None:
            flow = await self.target_resolver.get_workflow(
                schedule_create.workflow_id
            )
            if flow is None or flow.pod_id != schedule_create.pod_id:
                raise ScheduleValidationError("Workflow target not found in pod")
            if flow.is_global_workflow:
                existing = await self.schedule_repository.find_active_by_workflow(
                    pod_id=schedule_create.pod_id,
                    workflow_id=flow.id,
                    user_id=schedule_create.user_id,
                )
                if existing:
                    raise ScheduleValidationError(
                        "Global workflow already has a schedule for this user in this "
                        "pod (GLOBAL workflows are system-wide singletons). "
                        f"Conflicting schedule: '{existing[0].name}' "
                        f"({existing[0].id}). Update or delete it instead of "
                        "creating another."
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
        base = normalize_resource_name(f"{target_name}_{schedule_create.schedule_type.value.lower()}_schedule")
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
                conflicting = [
                    item for item in existing_for_workflow if item.id != schedule_id
                ]
                if conflicting:
                    raise ScheduleValidationError(
                        "Global workflow already has a schedule for this user in this "
                        "pod (GLOBAL workflows are system-wide singletons). "
                        f"Conflicting schedule: '{conflicting[0].name}' "
                        f"({conflicting[0].id}). Update or delete it instead of "
                        "creating another."
                    )
        updated = await self.schedule_repository.update(schedule_id, **update_data)

        if updated and update_data.get("is_active") is True:
            # Reactivating a schedule clears its circuit-breaker failure streak so
            # a re-enabled schedule starts fresh instead of tripping again on the
            # next failure.
            await self.schedule_repository.reset_consecutive_failures(schedule_id)

        if updated and updated.schedule_type == ScheduleType.TIME:
            if "config" in update_data or "is_active" in update_data:
                if updated.is_active:
                    await self.scheduler_service.schedule_job(updated)
                else:
                    await self.scheduler_service.remove_job(updated.id)

        return updated

    async def delete_schedule(self, schedule_id: UUID) -> bool:
        """Delete a schedule and remove external/scheduler side effects."""
        existing = await self.schedule_repository.get(schedule_id)
        if not existing:
            return False

        if existing.schedule_type == ScheduleType.TIME:
            await self.scheduler_service.remove_job(schedule_id)

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
                logger.exception(
                    "Failed to delete external schedule for %s", schedule_id
                )
                raise ScheduleInfrastructureError(
                    f"Failed to delete external schedule for {schedule_id}: {exc}"
                ) from exc

        return await self.schedule_repository.delete(schedule_id)

    async def delete_all_for_pod(self, pod_id: UUID) -> int:
        """Delete every schedule in a pod with full teardown (cleanup-only).

        System-level: no RBAC filtering, includes internal schedules. Best-effort
        per schedule so one external-teardown failure (APScheduler/Composio) does
        not abort the rest; on failure the row is force-deleted so the schedule
        can no longer fire.
        """
        schedules = await self.schedule_repository.list_all_by_pod(pod_id)
        deleted = 0
        for schedule in schedules:
            try:
                if await self.delete_schedule(schedule.id):
                    deleted += 1
            except Exception:
                logger.exception(
                    "Failed to delete schedule %s during pod %s cleanup",
                    schedule.id,
                    pod_id,
                )
                try:
                    if await self.schedule_repository.delete(schedule.id):
                        deleted += 1
                except Exception:
                    logger.exception(
                        "Fallback delete failed for schedule %s", schedule.id
                    )
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
                await self._get_agent_by_name(pod_id=pod_id, agent_name=agent_name)
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
            await ctx.require(
                Permissions.AGENT_EXECUTE,
                ResourceRef(
                    resource_type=ResourceType.AGENT,
                    resource_id=schedule.agent_id,
                    pod_id=schedule.pod_id,
                ),
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
