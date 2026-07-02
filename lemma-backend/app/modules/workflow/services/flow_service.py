from uuid import UUID
from typing import List

from app.core.authorization.context import (
    ActorType,
    Context,
    ResourceRef,
    ResourceType,
    ResourceVisibility,
)
from app.core.authorization.permissions import Permissions
from app.core.helpers.slug import normalize_resource_name
from app.modules.icon.services.icon_service import IconService
from app.modules.workflow.domain.flow import (
    FlowEntity,
    FlowSummaryEntity,
    FlowUpdateEntity,
    WorkflowMode,
)
from app.modules.workflow.domain.graph import WorkflowEdge
from app.modules.workflow.domain.nodes import WorkflowNode
from app.modules.workflow.domain.errors import (
    WorkflowConflictError,
    WorkflowValidationError,
)
from app.modules.workflow.domain.start import FlowStart
from app.modules.workflow.infrastructure.repositories import SqlAlchemyFlowRepository
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork


class FlowService:
    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        _schedule_adapter: object | None = None,
        authorization_service: object | None = None,
        icon_service: IconService | None = None,
    ):
        self.uow = uow
        self.flow_repo = SqlAlchemyFlowRepository(uow)
        self.authorization_service = authorization_service
        self.icon_service = icon_service

    async def _require_action(
        self,
        *,
        requester_user_id: UUID | None,
        action: str,
        pod_id: UUID,
        flow_id: UUID | None = None,
        ctx: Context | None = None,
    ) -> None:
        if ctx is not None:
            await ctx.require(
                action,
                ResourceRef(
                    resource_type=ResourceType.WORKFLOW if flow_id else ResourceType.POD,
                    resource_id=flow_id or pod_id,
                    pod_id=pod_id,
                ),
            )
            return

        if requester_user_id is not None:
            raise RuntimeError("Context is required for workflow authorization")

    async def create_flow(
        self,
        pod_id: UUID,
        name: str,
        description: str | None = None,
        icon_url: str | None = None,
        start: FlowStart | None = None,
        mode: WorkflowMode = WorkflowMode.GLOBAL,
        visibility: ResourceVisibility | str | None = None,
        nodes: List[WorkflowNode] | None = None,
        edges: List[WorkflowEdge] | None = None,
        requester_user_id: UUID | None = None,
        ctx: Context | None = None,
    ) -> FlowEntity:
        await self._require_action(
            requester_user_id=requester_user_id,
            action=Permissions.WORKFLOW_CREATE,
            pod_id=pod_id,
            ctx=ctx,
        )
        normalized_name = normalize_resource_name(name)
        existing = await self.flow_repo.get_by_name(pod_id, normalized_name)
        if existing:
            raise WorkflowConflictError(
                f"Workflow with name '{normalized_name}' already exists in pod {pod_id}"
            )
        normalized_visibility = self._normalize_workflow_visibility(visibility)
        flow = FlowEntity(
            pod_id=pod_id,
            user_id=requester_user_id,
            name=normalized_name,
            description=description,
            icon_url=icon_url,
            start=start,
            mode=mode,
            visibility=normalized_visibility,
            nodes=nodes or [],
            edges=edges or [],
        )
        # Validation raises GraphValidationError (422) listing every issue and
        # stamps entry_node_id. Empty shells skip validation until a graph is
        # uploaded via update_flow_graph.
        flow.validate_graph()
        return await self.flow_repo.create(flow)

    async def get_flow_by_name(
        self,
        pod_id: UUID,
        name: str,
        requester_user_id: UUID | None = None,
        ctx: Context | None = None,
    ) -> FlowEntity | None:
        flow = await self.flow_repo.get_by_name(pod_id, name, ctx=ctx)
        if flow and requester_user_id is not None:
            await self._require_action(
                requester_user_id=requester_user_id,
                action=Permissions.WORKFLOW_READ,
                pod_id=flow.pod_id,
                flow_id=flow.id,
                ctx=ctx,
            )
        return flow

    async def get_flow(
        self,
        flow_id: UUID,
        requester_user_id: UUID | None = None,
        ctx: Context | None = None,
    ) -> FlowEntity | None:
        flow = await self.flow_repo.get(flow_id, ctx=ctx)
        if flow and requester_user_id is not None:
            await self._require_action(
                requester_user_id=requester_user_id,
                action=Permissions.WORKFLOW_READ,
                pod_id=flow.pod_id,
                flow_id=flow.id,
                ctx=ctx,
            )
        return flow

    async def update_flow(
        self,
        flow_id: UUID,
        update_data: FlowUpdateEntity,
        requester_user_id: UUID | None = None,
        ctx: Context | None = None,
    ) -> FlowEntity:
        flow = await self.flow_repo.get(flow_id, ctx=ctx)
        if not flow:
            raise ValueError("Flow not found")
        await self._require_action(
            requester_user_id=requester_user_id,
            action=Permissions.WORKFLOW_UPDATE,
            pod_id=flow.pod_id,
            flow_id=flow.id,
            ctx=ctx,
        )
        old_icon_url = flow.icon_url

        if "description" in update_data.model_fields_set:
            flow.description = update_data.description
        if "icon_url" in update_data.model_fields_set:
            flow.icon_url = update_data.icon_url
        if "mode" in update_data.model_fields_set:
            flow.mode = update_data.mode
        if "start" in update_data.model_fields_set:
            flow.start = update_data.start
        if "visibility" in update_data.model_fields_set:
            flow.visibility = self._normalize_workflow_visibility(update_data.visibility)

        updated = await self.flow_repo.update(flow)
        if self.icon_service and old_icon_url != updated.icon_url:
            await self.icon_service.delete_by_url(old_icon_url)
        return updated

    @staticmethod
    def _normalize_workflow_visibility(value: ResourceVisibility | str | None) -> str:
        if value is None:
            return ResourceVisibility.POD.value
        raw = value.value if isinstance(value, ResourceVisibility) else str(value)
        try:
            visibility = ResourceVisibility(raw.upper())
        except ValueError as exc:
            raise WorkflowValidationError(f"Invalid visibility: {value}") from exc
        return visibility.value

    async def update_flow_graph(
        self,
        flow_id: UUID,
        nodes: List[WorkflowNode],
        edges: List[WorkflowEdge],
        start: FlowStart | None = None,
        requester_user_id: UUID | None = None,
        ctx: Context | None = None,
    ) -> FlowEntity:
        flow = await self.flow_repo.get(flow_id, ctx=ctx)
        if not flow:
            raise ValueError("Flow not found")
        await self._require_action(
            requester_user_id=requester_user_id,
            action=Permissions.WORKFLOW_UPDATE,
            pod_id=flow.pod_id,
            flow_id=flow.id,
            ctx=ctx,
        )

        flow.nodes = nodes
        flow.edges = edges
        if start:
            flow.start = start

        # Raises GraphValidationError and stamps entry_node_id.
        flow.validate_graph()

        return await self.flow_repo.update(flow)

    async def list_flows(
        self,
        pod_id: UUID,
        *,
        limit: int = 100,
        cursor: UUID | None = None,
        requester_user_id: UUID | None = None,
        ctx: Context | None = None,
    ) -> tuple[List[FlowEntity], UUID | None]:
        if ctx is None:
            raise RuntimeError("Context is required for workflow listing")
        await self._require_action(
            requester_user_id=requester_user_id,
            action=Permissions.WORKFLOW_READ,
            pod_id=pod_id,
            ctx=ctx,
        )
        return await self.flow_repo.list_visible_by_pod(
            pod_id,
            ctx=ctx,
            limit=limit,
            cursor=cursor,
        )

    async def list_flow_summaries(
        self,
        pod_id: UUID,
        *,
        limit: int = 100,
        cursor: UUID | None = None,
        requester_user_id: UUID | None = None,
        ctx: Context | None = None,
    ) -> tuple[List["FlowSummaryEntity"], UUID | None]:
        if ctx is None:
            raise RuntimeError("Context is required for workflow listing")
        await self._require_action(
            requester_user_id=requester_user_id,
            action=Permissions.WORKFLOW_READ,
            pod_id=pod_id,
            ctx=ctx,
        )
        return await self.flow_repo.list_summaries_visible_by_pod(
            pod_id,
            ctx=ctx,
            limit=limit,
            cursor=cursor,
        )

    async def delete_flow(
        self,
        flow_id: UUID,
        requester_user_id: UUID | None = None,
        ctx: Context | None = None,
    ) -> None:
        flow = await self.flow_repo.get(flow_id, ctx=ctx)
        old_icon_url = flow.icon_url if flow else None
        # A delegated workload always routes through authz — workflow.delete is
        # destructive and gated — so the owner shortcut can't let it bypass.
        is_delegated = ctx is not None and (
            ctx.actor_type == ActorType.DELEGATED_USER_WORKLOAD
        )
        if (
            flow
            and requester_user_id is not None
            and (is_delegated or flow.user_id != requester_user_id)
        ):
            await self._require_action(
                requester_user_id=requester_user_id,
                action=Permissions.WORKFLOW_DELETE,
                pod_id=flow.pod_id,
                flow_id=flow.id,
                ctx=ctx,
            )
        await self.flow_repo.delete(flow_id)
        if self.icon_service:
            await self.icon_service.delete_by_url(old_icon_url)
