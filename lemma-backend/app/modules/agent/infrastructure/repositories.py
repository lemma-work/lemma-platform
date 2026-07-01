"""Repositories for the unified agent module."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum
from uuid import UUID

from sqlalchemy import func, literal, select, update
from sqlalchemy.dialects.postgresql import JSONB, array
from sqlalchemy.orm import selectinload

from app.core.authorization.context import Context, ResourceType, ResourceVisibility
from app.core.authorization.grants import (
    delete_grantee_grants,
    delete_resource_grants,
    delete_resource_sharing_grants,
)
from app.core.authorization.permissions import Permissions
from app.core.authorization.sql_actions import (
    allowed_actions_contains,
    allowed_actions_expr,
)
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.events import AgentDomainEvent
from app.modules.agent.domain.entities import (
    Agent as AgentEntity,
    AgentRun as AgentRunEntity,
    Conversation as ConversationEntity,
    Message as MessageEntity,
)
from app.modules.agent.domain.runtime_profiles import (
    AgentRuntimeProfile,
    RuntimeProfileScope,
    RuntimeProfileStatus,
    reveal_credentials,
)
from app.modules.agent.domain.value_objects import (
    ACTIVE_AGENT_RUN_STATUSES,
    TERMINAL_AGENT_RUN_STATUSES,
    AgentRuntimeConfig,
    AgentRunApprovalDecision,
    AgentRunFinishResult,
    AgentRunStatus,
    ConversationStatus,
    ConversationType,
    JsonObject,
    JsonValue,
    MessageDraft,
    to_json_value,
)
from app.modules.agent.infrastructure.models import (
    AgentApprovalDecisionModel,
    AgentModel,
    AgentRuntimeDaemonModel,
    AgentRuntimeProfileModel,
    AgentRunModel,
    ConversationModel,
    MessageModel,
)
from app.modules.connectors.domain.ports import SecretEncryptionPort


def _enum_status_values_for_db(statuses: object, enum_type: type[Enum]) -> list[str]:
    if isinstance(statuses, (enum_type, str)):
        normalized_statuses = [statuses]
    else:
        normalized_statuses = list(statuses)
    values: list[str] = []
    for status in normalized_statuses:
        status = enum_type(status)
        values.extend([status.value, status.value.lower()])
    return list(dict.fromkeys(values))


def _run_status_values_for_db(statuses: object) -> list[str]:
    return _enum_status_values_for_db(statuses, AgentRunStatus)


def _conversation_status_values_for_db(statuses: object) -> list[str]:
    return _enum_status_values_for_db(statuses, ConversationStatus)


_ACTIVE_AGENT_RUN_STATUS_VALUES = _run_status_values_for_db(ACTIVE_AGENT_RUN_STATUSES)


class AgentRuntimeProfileRepository:
    """Repository for organization-owned runtime profiles."""

    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        encryption: SecretEncryptionPort,
    ):
        self.uow = uow
        self.session = uow.session
        self.encryption = encryption

    @staticmethod
    def _serialize_json(value: object | None) -> dict | None:
        if value is None:
            return None
        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            return model_dump(mode="json")
        if isinstance(value, dict):
            return value
        return None

    def _to_entity(self, instance: AgentRuntimeProfileModel) -> AgentRuntimeProfile:
        data = instance.to_entity().model_dump(mode="json")
        data["credentials"] = self.encryption.decrypt_json(instance.credentials)
        return AgentRuntimeProfile.model_validate(data)

    def _to_model(self, entity: AgentRuntimeProfile) -> AgentRuntimeProfileModel:
        return AgentRuntimeProfileModel(
            organization_id=entity.organization_id,
            user_id=entity.user_id,
            daemon_id=entity.daemon_id,
            scope=entity.scope.value,
            kind=entity.kind.value,
            protocol=entity.protocol.value,
            name=entity.name,
            description=entity.description,
            default_model_name=entity.default_model_name,
            model_catalog=[
                item.model_dump(mode="json") for item in entity.model_catalog
            ],
            config=self._serialize_json(entity.config) or {},
            # reveal_credentials (not _serialize_json) so SecretStr fields persist
            # as their plaintext before encryption — model_dump(mode="json") would
            # otherwise store the masked "**********" and lose the real key.
            credentials=self.encryption.encrypt_json(
                reveal_credentials(entity.credentials)
            ),
            status=entity.status.value,
            profile_metadata=entity.metadata,
        )

    async def create(self, entity: AgentRuntimeProfile) -> AgentRuntimeProfile:
        from sqlalchemy.exc import IntegrityError

        instance = self._to_model(entity)
        self.session.add(instance)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            raise RuntimeError(
                f"Runtime profile named {entity.name!r} already exists"
            ) from exc
        return self._to_entity(instance)

    async def get_visible(
        self,
        *,
        organization_id: UUID,
        user_id: UUID,
        include_disabled: bool = False,
    ) -> list[AgentRuntimeProfile]:
        stmt = select(AgentRuntimeProfileModel).where(
            AgentRuntimeProfileModel.organization_id == organization_id,
            (
                AgentRuntimeProfileModel.scope
                == RuntimeProfileScope.ORGANIZATION.value
            )
            | (
                (
                    AgentRuntimeProfileModel.scope
                    == RuntimeProfileScope.PERSONAL.value
                )
                & (AgentRuntimeProfileModel.user_id == user_id)
            ),
        )
        if not include_disabled:
            stmt = stmt.where(
                AgentRuntimeProfileModel.status == RuntimeProfileStatus.ACTIVE.value
            )
        stmt = stmt.order_by(
            AgentRuntimeProfileModel.name.asc(),
        )
        result = await self.session.execute(stmt)
        return [self._to_entity(instance) for instance in result.scalars()]

    async def get_visible_by_id(
        self,
        *,
        profile_id: str,
        organization_id: UUID,
        user_id: UUID,
    ) -> AgentRuntimeProfile | None:
        try:
            profile_uuid = UUID(profile_id)
        except ValueError:
            return None
        stmt = (
            select(AgentRuntimeProfileModel)
            .where(
                AgentRuntimeProfileModel.id == profile_uuid,
                AgentRuntimeProfileModel.organization_id == organization_id,
                (
                    AgentRuntimeProfileModel.scope
                    == RuntimeProfileScope.ORGANIZATION.value
                )
                | (
                    (
                        AgentRuntimeProfileModel.scope
                        == RuntimeProfileScope.PERSONAL.value
                    )
                    & (AgentRuntimeProfileModel.user_id == user_id)
                ),
                AgentRuntimeProfileModel.status == RuntimeProfileStatus.ACTIVE.value,
            )
            .limit(1)
        )
        result = await self.session.execute(stmt)
        instance = result.scalar_one_or_none()
        return self._to_entity(instance) if instance else None


class AgentRuntimeDaemonRepository:
    """Repository for user-owned daemon catalog rows."""

    def __init__(self, uow: SqlAlchemyUnitOfWork):
        self.uow = uow
        self.session = uow.session

    async def upsert_ready(
        self,
        *,
        user_id: UUID,
        device_key: str,
        display_name: str,
        device_info: JsonObject | None = None,
        harness_catalog: JsonObject | None = None,
    ) -> AgentRuntimeDaemonModel:
        now = datetime.now(timezone.utc)
        normalized_device_key = device_key.strip()
        normalized_display_name = display_name.strip() or "Lemma daemon"
        stmt = (
            select(AgentRuntimeDaemonModel)
            .where(
                AgentRuntimeDaemonModel.user_id == user_id,
                AgentRuntimeDaemonModel.device_key == normalized_device_key,
            )
            .limit(1)
        )
        result = await self.session.execute(stmt)
        instance = result.scalar_one_or_none()
        if instance is None:
            instance = AgentRuntimeDaemonModel(
                user_id=user_id,
                device_key=normalized_device_key,
                display_name=normalized_display_name,
                status="ONLINE",
                device_info=device_info or {},
                harness_catalog=harness_catalog or {},
                last_seen_at=now,
                connected_at=now,
                disconnected_at=None,
            )
            self.session.add(instance)
        else:
            instance.display_name = normalized_display_name
            instance.status = "ONLINE"
            instance.device_info = device_info or {}
            instance.harness_catalog = harness_catalog or {}
            instance.last_seen_at = now
            instance.connected_at = now
            instance.disconnected_at = None
        await self.session.flush()
        return instance

    async def update_catalog(
        self,
        *,
        daemon_id: UUID,
        user_id: UUID,
        harness_catalog: JsonObject,
    ) -> AgentRuntimeDaemonModel | None:
        instance = await self.get_for_user(daemon_id=daemon_id, user_id=user_id)
        if instance is None:
            return None
        instance.harness_catalog = harness_catalog
        instance.last_seen_at = datetime.now(timezone.utc)
        await self.session.flush()
        return instance

    async def mark_seen(
        self,
        *,
        daemon_id: UUID,
        user_id: UUID,
    ) -> AgentRuntimeDaemonModel | None:
        instance = await self.get_for_user(daemon_id=daemon_id, user_id=user_id)
        if instance is None:
            return None
        instance.last_seen_at = datetime.now(timezone.utc)
        await self.session.flush()
        return instance

    async def mark_offline(
        self,
        *,
        daemon_id: UUID,
        user_id: UUID,
    ) -> AgentRuntimeDaemonModel | None:
        instance = await self.get_for_user(daemon_id=daemon_id, user_id=user_id)
        if instance is None:
            return None
        now = datetime.now(timezone.utc)
        instance.status = "OFFLINE"
        instance.last_seen_at = now
        instance.disconnected_at = now
        await self.session.flush()
        return instance

    async def get_for_user(
        self,
        *,
        daemon_id: UUID,
        user_id: UUID,
    ) -> AgentRuntimeDaemonModel | None:
        stmt = (
            select(AgentRuntimeDaemonModel)
            .where(
                AgentRuntimeDaemonModel.id == daemon_id,
                AgentRuntimeDaemonModel.user_id == user_id,
            )
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_user(
        self,
        *,
        user_id: UUID,
    ) -> list[AgentRuntimeDaemonModel]:
        result = await self.session.execute(
            select(AgentRuntimeDaemonModel)
            .where(AgentRuntimeDaemonModel.user_id == user_id)
            .order_by(
                AgentRuntimeDaemonModel.status.desc(),
                AgentRuntimeDaemonModel.updated_at.desc(),
            )
        )
        return list(result.scalars())


class AgentRepository:
    """Repository for pod-owned agents."""

    def __init__(self, uow: SqlAlchemyUnitOfWork):
        self.uow = uow
        self.session = uow.session

    async def create(self, agent: AgentEntity) -> AgentEntity:
        model = AgentModel(
            id=agent.id,
            created_at=agent.created_at,
            updated_at=agent.updated_at,
            pod_id=agent.pod_id,
            user_id=agent.user_id,
            name=agent.name,
            description=agent.description,
            icon_url=agent.icon_url,
            visibility=agent.visibility,
            instruction=agent.instruction,
            agent_runtime=(
                agent.agent_runtime.model_dump(mode="json")
                if agent.agent_runtime
                else None
            ),
            toolsets=[toolset.value for toolset in agent.toolsets],
            input_schema=agent.input_schema,
            output_schema=agent.output_schema,
            agent_metadata=agent.metadata,
        )
        self.session.add(model)
        await self.session.flush()
        return model.to_entity()

    def _to_entity_with_allowed_actions(
        self,
        model: AgentModel,
        allowed_actions: list[str] | tuple[str, ...] | None = None,
    ) -> AgentEntity:
        entity = model.to_entity()
        if allowed_actions is not None:
            entity.allowed_actions = list(allowed_actions)
        return entity

    async def get(self, agent_id: UUID, ctx: Context | None = None) -> AgentEntity | None:
        if ctx is None:
            result = await self.session.execute(
                select(AgentModel).where(AgentModel.id == agent_id)
            )
            model = result.scalar_one_or_none()
            return model.to_entity() if model else None
        actions = allowed_actions_expr(
            ctx=ctx,
            resource_type=ResourceType.AGENT,
            resource_id_col=AgentModel.id,
            pod_id_col=AgentModel.pod_id,
            owner_user_id_col=AgentModel.user_id,
            visibility_col=AgentModel.visibility,
        )
        result = await self.session.execute(
            select(AgentModel, actions).where(AgentModel.id == agent_id)
        )
        row = result.one_or_none()
        return self._to_entity_with_allowed_actions(row[0], row[1]) if row else None

    async def update(self, agent: AgentEntity) -> AgentEntity:
        result = await self.session.execute(
            select(AgentModel).where(AgentModel.id == agent.id)
        )
        model = result.scalar_one_or_none()
        if model is None:
            return agent

        model.name = agent.name
        model.description = agent.description
        model.icon_url = agent.icon_url
        previous_visibility = model.visibility
        model.visibility = agent.visibility
        if (
            previous_visibility == ResourceVisibility.RESTRICTED.value
            and agent.visibility != ResourceVisibility.RESTRICTED.value
        ):
            await delete_resource_sharing_grants(
                self.session,
                pod_id=agent.pod_id,
                resource_type=ResourceType.AGENT,
                resource_id=agent.id,
            )
        model.instruction = agent.instruction
        model.agent_runtime = (
            agent.agent_runtime.model_dump(mode="json")
            if agent.agent_runtime
            else None
        )
        model.toolsets = [toolset.value for toolset in agent.toolsets]
        model.input_schema = agent.input_schema
        model.output_schema = agent.output_schema
        model.agent_metadata = agent.metadata
        await self.session.flush()
        return model.to_entity()

    async def delete(self, agent_id: UUID) -> None:
        result = await self.session.execute(
            select(AgentModel).where(AgentModel.id == agent_id)
        )
        model = result.scalar_one_or_none()
        if model is not None:
            if model.pod_id is not None:
                await delete_resource_grants(
                    self.session,
                    pod_id=model.pod_id,
                    resource_type=ResourceType.AGENT,
                    resource_id=agent_id,
                )
                await delete_grantee_grants(
                    self.session,
                    pod_id=model.pod_id,
                    grantee_type="AGENT",
                    grantee_id=agent_id,
                )
            await self.session.delete(model)
            await self.session.flush()

    async def list_by_pod(
        self,
        *,
        pod_id: UUID,
        cursor: UUID | None = None,
        limit: int = 100,
    ) -> tuple[list[AgentEntity], UUID | None]:
        stmt = select(AgentModel).where(AgentModel.pod_id == pod_id)
        if cursor is not None:
            stmt = stmt.where(AgentModel.id < cursor)
        stmt = stmt.order_by(AgentModel.id.desc()).limit(limit + 1)
        result = await self.session.execute(stmt)
        rows = list(result.scalars())
        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]
        next_cursor = rows[-1].id if has_more and rows else None
        return [row.to_entity() for row in rows], next_cursor

    async def list_visible_by_pod(
        self,
        *,
        pod_id: UUID,
        ctx: Context,
        cursor: UUID | None = None,
        limit: int = 100,
    ) -> tuple[list[AgentEntity], UUID | None]:
        actions = allowed_actions_expr(
            ctx=ctx,
            resource_type=ResourceType.AGENT,
            resource_id_col=AgentModel.id,
            pod_id_col=AgentModel.pod_id,
            owner_user_id_col=AgentModel.user_id,
            visibility_col=AgentModel.visibility,
        )
        stmt = select(AgentModel, actions).where(
            AgentModel.pod_id == pod_id,
            allowed_actions_contains(actions, Permissions.AGENT_READ),
        )
        if cursor is not None:
            stmt = stmt.where(AgentModel.id < cursor)
        stmt = stmt.order_by(AgentModel.id.desc()).limit(limit + 1)
        result = await self.session.execute(stmt)
        rows = list(result.all())
        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]
        next_cursor = rows[-1][0].id if has_more and rows else None
        return [
            self._to_entity_with_allowed_actions(model, actions)
            for model, actions in rows
        ], next_cursor

    async def get_by_pod_and_name(
        self, *, pod_id: UUID, name: str, ctx: Context | None = None
    ) -> AgentEntity | None:
        if ctx is None:
            result = await self.session.execute(
                select(AgentModel).where(
                    AgentModel.pod_id == pod_id, AgentModel.name == name
                )
            )
            model = result.scalar_one_or_none()
            return model.to_entity() if model else None
        actions = allowed_actions_expr(
            ctx=ctx,
            resource_type=ResourceType.AGENT,
            resource_id_col=AgentModel.id,
            pod_id_col=AgentModel.pod_id,
            owner_user_id_col=AgentModel.user_id,
            visibility_col=AgentModel.visibility,
        )
        result = await self.session.execute(
            select(AgentModel, actions).where(
                AgentModel.pod_id == pod_id, AgentModel.name == name
            )
        )
        row = result.one_or_none()
        return self._to_entity_with_allowed_actions(row[0], row[1]) if row else None


class ConversationRepository:
    """Repository for conversations, agent runs, and messages."""

    def __init__(self, uow: SqlAlchemyUnitOfWork):
        self.uow = uow
        self.session = uow.session

    def collect_events(self, events: list[AgentDomainEvent]) -> None:
        self.uow.collect_events(events)

    async def create_conversation(
        self,
        conversation: ConversationEntity,
    ) -> ConversationEntity:
        model = ConversationModel(
            id=conversation.id,
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
            user_id=conversation.user_id,
            pod_id=conversation.pod_id,
            organization_id=conversation.organization_id,
            agent_id=conversation.agent_id,
            title=conversation.title,
            instructions=conversation.instructions,
            agent_runtime=(
                conversation.agent_runtime.model_dump(mode="json")
                if conversation.agent_runtime
                else None
            ),
            conversation_type=conversation.type.value,
            status=conversation.status.value if conversation.status else None,
            output_data=conversation.output,
            parent_id=conversation.parent_id,
            conversation_metadata=conversation.metadata,
        )
        self.session.add(model)
        await self.session.flush()
        return model.to_entity()

    async def update_conversation(
        self,
        conversation: ConversationEntity,
    ) -> ConversationEntity:
        result = await self.session.execute(
            select(ConversationModel).where(ConversationModel.id == conversation.id)
        )
        model = result.scalar_one_or_none()
        if model is None:
            return conversation

        model.title = conversation.title
        model.instructions = conversation.instructions
        model.agent_runtime = (
            conversation.agent_runtime.model_dump(mode="json")
            if conversation.agent_runtime
            else None
        )
        model.conversation_type = conversation.type.value
        model.status = conversation.status.value if conversation.status else None
        model.output_data = conversation.output
        model.conversation_metadata = conversation.metadata
        await self.session.flush()
        return model.to_entity()

    async def get_conversation_metadata_key(
        self,
        conversation_id: UUID,
        key: str,
    ) -> JsonValue | None:
        """Read a single key out of a conversation's metadata JSON blob."""
        result = await self.session.execute(
            select(ConversationModel.conversation_metadata).where(
                ConversationModel.id == conversation_id
            )
        )
        metadata = result.scalar_one_or_none()
        if not isinstance(metadata, dict):
            return None
        return metadata.get(key)

    async def set_conversation_metadata_key(
        self,
        conversation_id: UUID,
        key: str,
        value: JsonValue,
    ) -> None:
        """Write a single metadata key without clobbering sibling keys.

        Uses ``jsonb_set`` so concurrent writers touching other keys (e.g. the
        ``is_sub_agent`` / ``surface_platform`` flags) are never overwritten.

        ``value`` must be bound with the ``JSONB`` type directly (``literal``,
        not ``cast(json.dumps(value), JSONB)``) — casting an already-serialized
        JSON string double-encodes it, so ``jsonb_set`` stores a JSON *string*
        scalar (the dumped text) instead of the intended array/object.
        """
        stmt = (
            update(ConversationModel)
            .where(ConversationModel.id == conversation_id)
            .values(
                conversation_metadata=func.jsonb_set(
                    func.coalesce(
                        ConversationModel.conversation_metadata,
                        literal({}, JSONB),
                    ),
                    array([key]),
                    literal(value, JSONB),
                    True,
                )
            )
        )
        await self.session.execute(stmt)
        await self.session.flush()

    async def get_conversation(
        self,
        conversation_id: UUID,
        *,
        include_messages: bool = False,
        include_runs: bool = False,
    ) -> ConversationEntity | None:
        stmt = select(ConversationModel).where(ConversationModel.id == conversation_id)
        if include_messages:
            stmt = stmt.options(selectinload(ConversationModel.messages))
        if include_runs:
            stmt = stmt.options(selectinload(ConversationModel.agent_runs))
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        return model.to_entity() if model else None

    async def list_conversations(
        self,
        *,
        user_id: UUID,
        pod_id: UUID,
        agent_id: UUID | None,
        status: ConversationStatus | None = None,
        conversation_type: ConversationType | None = None,
        metadata_filters: JsonObject | None = None,
        parent_id: UUID | None = None,
        cursor: UUID | None = None,
        limit: int = 20,
    ) -> tuple[list[ConversationEntity], UUID | None]:
        stmt = select(ConversationModel).where(
            ConversationModel.user_id == user_id,
            ConversationModel.pod_id == pod_id,
        )
        # Default: root conversations only. With parent_id: that conversation's
        # children (sub-agent conversations).
        if parent_id is None:
            stmt = stmt.where(ConversationModel.parent_id.is_(None))
        else:
            stmt = stmt.where(ConversationModel.parent_id == parent_id)
        if agent_id is not None:
            stmt = stmt.where(ConversationModel.agent_id == agent_id)
        elif parent_id is None:
            # Root list with no agent filter → the default pod agent's
            # conversations (agent_id IS NULL). Children, by contrast, belong to
            # whatever sub-agent was spawned, so don't constrain their agent_id.
            stmt = stmt.where(ConversationModel.agent_id.is_(None))
        if status is not None:
            stmt = stmt.where(
                ConversationModel.status.in_(_conversation_status_values_for_db(status))
            )
        if conversation_type is not None:
            stmt = stmt.where(
                ConversationModel.conversation_type == conversation_type.value
            )
        if metadata_filters:
            stmt = stmt.where(
                ConversationModel.conversation_metadata.op("@>")(metadata_filters)
            )
        return await self._list_conversations(stmt, cursor=cursor, limit=limit)

    async def list_children(
        self,
        *,
        parent_id: UUID,
        user_id: UUID,
        limit: int = 50,
        include_runs: bool = True,
    ) -> list[ConversationEntity]:
        """List child (sub-agent) conversations of a parent, newest first.

        Inverse of list_conversations (which hides children via parent_id IS NULL);
        reuses the ix_agent_conv_parent index. Scoped to the owning user.
        """
        stmt = (
            select(ConversationModel)
            .where(
                ConversationModel.parent_id == parent_id,
                ConversationModel.user_id == user_id,
            )
            .order_by(
                ConversationModel.created_at.desc(),
                ConversationModel.id.desc(),
            )
            .limit(limit)
        )
        if include_runs:
            stmt = stmt.options(selectinload(ConversationModel.agent_runs))
        result = await self.session.execute(stmt)
        return [row.to_entity() for row in result.scalars()]

    async def _list_conversations(
        self,
        stmt,
        *,
        cursor: UUID | None,
        limit: int,
    ) -> tuple[list[ConversationEntity], UUID | None]:
        if cursor is not None:
            stmt = stmt.where(ConversationModel.id < cursor)
        stmt = stmt.order_by(ConversationModel.id.desc()).limit(limit + 1)
        result = await self.session.execute(stmt)
        rows = list(result.scalars())
        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]
        next_cursor = rows[-1].id if has_more and rows else None
        return [row.to_entity() for row in rows], next_cursor

    async def create_agent_run(
        self,
        *,
        conversation_id: UUID,
        agent_id: UUID | None,
        agent_runtime: AgentRuntimeConfig,
        parent_run_id: UUID | None = None,
        metadata: JsonObject | None = None,
    ) -> AgentRunEntity:
        now = datetime.now(timezone.utc)
        model = AgentRunModel(
            conversation_id=conversation_id,
            agent_id=agent_id,
            parent_run_id=parent_run_id,
            status=AgentRunStatus.RUNNING.value,
            agent_runtime=agent_runtime.model_dump(mode="json"),
            started_at=now,
            run_metadata=metadata,
        )
        self.session.add(model)
        await self._update_conversation_status(
            conversation_id=conversation_id,
            status=ConversationStatus.RUNNING,
            output_data=None,
        )
        await self.session.flush()
        return model.to_entity()

    async def get_active_agent_run_for_update(
        self,
        conversation_id: UUID,
    ) -> AgentRunEntity | None:
        result = await self.session.execute(
            select(AgentRunModel)
            .where(
                AgentRunModel.conversation_id == conversation_id,
                AgentRunModel.status.in_(_ACTIVE_AGENT_RUN_STATUS_VALUES),
            )
            .order_by(AgentRunModel.created_at.desc(), AgentRunModel.id.desc())
            .limit(1)
            .with_for_update()
        )
        model = result.scalar_one_or_none()
        return model.to_entity() if model else None

    async def get_active_agent_run(
        self,
        conversation_id: UUID,
    ) -> AgentRunEntity | None:
        result = await self.session.execute(
            select(AgentRunModel)
            .where(
                AgentRunModel.conversation_id == conversation_id,
                AgentRunModel.status.in_(_ACTIVE_AGENT_RUN_STATUS_VALUES),
            )
            .order_by(AgentRunModel.created_at.desc(), AgentRunModel.id.desc())
            .limit(1)
        )
        model = result.scalar_one_or_none()
        return model.to_entity() if model else None

    async def list_stale_active_runs(
        self,
        *,
        cutoff_seconds: int,
        limit: int = 200,
    ) -> list[AgentRunEntity]:
        """List non-terminal agent runs whose ``started_at`` predates the cutoff.

        These are runs whose worker task died (crash/OOM/forced shutdown) without
        finalizing them — they would otherwise sit in RUNNING forever. The cutoff
        must exceed the streaq task timeout so legitimately long-running agents
        are never swept up.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=cutoff_seconds)
        result = await self.session.execute(
            select(AgentRunModel)
            .where(
                AgentRunModel.status.in_(_ACTIVE_AGENT_RUN_STATUS_VALUES),
                AgentRunModel.started_at < cutoff,
            )
            .order_by(AgentRunModel.started_at.asc())
            .limit(limit)
        )
        return [model.to_entity() for model in result.scalars().all()]

    async def lock_conversation(self, conversation_id: UUID) -> None:
        await self.session.execute(
            select(ConversationModel.id)
            .where(ConversationModel.id == conversation_id)
            .with_for_update()
        )

    async def list_agent_runs_with_messages(
        self,
        conversation_id: UUID,
    ) -> list[AgentRunEntity]:
        result = await self.session.execute(
            select(AgentRunModel)
            .where(AgentRunModel.conversation_id == conversation_id)
            .options(selectinload(AgentRunModel.messages))
            .order_by(AgentRunModel.created_at.asc(), AgentRunModel.id.asc())
        )
        return [model.to_entity() for model in result.scalars()]

    async def list_agent_runs_with_messages_by_run_id(
        self,
        agent_run_id: UUID,
    ) -> list[AgentRunEntity]:
        conversation_id_result = await self.session.execute(
            select(AgentRunModel.conversation_id).where(
                AgentRunModel.id == agent_run_id
            )
        )
        conversation_id = conversation_id_result.scalar_one_or_none()
        if conversation_id is None:
            return []
        return await self.list_agent_runs_with_messages(conversation_id)

    async def get_agent_run(self, agent_run_id: UUID) -> AgentRunEntity | None:
        result = await self.session.execute(
            select(AgentRunModel).where(AgentRunModel.id == agent_run_id)
        )
        model = result.scalar_one_or_none()
        return model.to_entity() if model else None

    async def get_latest_agent_run_for_conversation(
        self,
        conversation_id: UUID,
    ) -> AgentRunEntity | None:
        result = await self.session.execute(
            select(AgentRunModel)
            .where(AgentRunModel.conversation_id == conversation_id)
            .order_by(AgentRunModel.created_at.desc(), AgentRunModel.id.desc())
            .limit(1)
        )
        model = result.scalar_one_or_none()
        return model.to_entity() if model else None

    async def append_message(
        self,
        *,
        conversation_id: UUID,
        agent_run_id: UUID | None,
        draft: MessageDraft,
    ) -> MessageEntity:
        lock_result = await self.session.execute(
            select(ConversationModel)
            .where(ConversationModel.id == conversation_id)
            .with_for_update()
        )
        conversation = lock_result.scalar_one()
        sequence_result = await self.session.execute(
            select(func.coalesce(func.max(MessageModel.sequence), -1)).where(
                MessageModel.conversation_id == conversation_id
            )
        )
        sequence = int(sequence_result.scalar_one()) + 1
        model = MessageModel(
            conversation_id=conversation.id,
            agent_run_id=agent_run_id,
            sequence=sequence,
            role=draft.role.value,
            kind=draft.kind.value,
            text=draft.text,
            tool_name=draft.tool_name,
            tool_call_id=draft.tool_call_id,
            tool_args=(
                to_json_value(draft.tool_args) if draft.tool_args is not None else None
            ),
            tool_result=(
                to_json_value(draft.tool_result)
                if draft.tool_result is not None
                else None
            ),
            message_metadata=draft.metadata,
        )
        self.session.add(model)
        await self.session.flush()
        return model.to_entity()

    async def list_messages(
        self,
        *,
        conversation_id: UUID,
        before_sequence: int | None = None,
        after_sequence: int | None = None,
        limit: int = 100,
    ) -> tuple[list[MessageEntity], int | None]:
        stmt = select(MessageModel).where(
            MessageModel.conversation_id == conversation_id
        )
        if before_sequence is not None:
            stmt = stmt.where(MessageModel.sequence < before_sequence)
        if after_sequence is not None:
            stmt = stmt.where(MessageModel.sequence > after_sequence)
        stmt = stmt.order_by(MessageModel.sequence.desc()).limit(limit + 1)
        result = await self.session.execute(stmt)
        rows = list(result.scalars())
        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]
        next_cursor = rows[-1].sequence if has_more and rows else None
        return [row.to_entity() for row in reversed(rows)], next_cursor

    async def record_approval_decision(
        self,
        *,
        conversation_id: UUID,
        approval_id: str,
        agent_run_id: UUID | None,
        tool_name: str | None,
        decision: AgentRunApprovalDecision,
        response: JsonObject | None,
        resolved_by_user_id: UUID,
    ) -> bool:
        """Persist a decision once. Returns False if already recorded."""
        existing = await self.session.execute(
            select(AgentApprovalDecisionModel.id).where(
                AgentApprovalDecisionModel.conversation_id == conversation_id,
                AgentApprovalDecisionModel.approval_id == approval_id,
            )
        )
        if existing.scalar_one_or_none() is not None:
            return False
        self.session.add(
            AgentApprovalDecisionModel(
                conversation_id=conversation_id,
                approval_id=approval_id,
                agent_run_id=agent_run_id,
                tool_name=tool_name,
                decision=decision.value,
                response=response or {},
                resolved_by_user_id=resolved_by_user_id,
            )
        )
        await self.session.flush()
        return True

    async def get_approval_decision(
        self,
        *,
        conversation_id: UUID,
        approval_id: str,
    ) -> tuple[AgentRunApprovalDecision, JsonObject] | None:
        result = await self.session.execute(
            select(AgentApprovalDecisionModel).where(
                AgentApprovalDecisionModel.conversation_id == conversation_id,
                AgentApprovalDecisionModel.approval_id == approval_id,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        response = row.response if isinstance(row.response, dict) else {}
        return AgentRunApprovalDecision(row.decision), response

    async def list_resolved_approval_ids(
        self,
        *,
        conversation_id: UUID,
    ) -> set[str]:
        result = await self.session.execute(
            select(AgentApprovalDecisionModel.approval_id).where(
                AgentApprovalDecisionModel.conversation_id == conversation_id
            )
        )
        return {row for row in result.scalars()}

    async def finish_agent_run(
        self,
        *,
        agent_run_id: UUID,
        status: AgentRunStatus,
        conversation_status: ConversationStatus | None = None,
        error: str | None = None,
        output_data: JsonValue | None = None,
    ) -> AgentRunFinishResult | None:
        result = await self.session.execute(
            select(AgentRunModel).where(AgentRunModel.id == agent_run_id)
        )
        model = result.scalar_one_or_none()
        if model is None:
            return None

        current_status = AgentRunStatus(model.status)
        resolved_conversation_status = conversation_status or ConversationStatus(
            current_status.value
        )
        if current_status in TERMINAL_AGENT_RUN_STATUSES:
            return AgentRunFinishResult(
                status=current_status,
                conversation_status=resolved_conversation_status,
                updated=False,
            )

        next_status = status
        if (
            current_status == AgentRunStatus.STOP_REQUESTED
            and status in TERMINAL_AGENT_RUN_STATUSES
        ):
            next_status = AgentRunStatus.STOPPED

        model.status = next_status.value
        model.error = error
        if output_data is not None:
            model.output_data = output_data
        resolved_conversation_status = conversation_status or ConversationStatus(
            next_status.value
        )
        await self._update_conversation_status(
            conversation_id=model.conversation_id,
            status=resolved_conversation_status,
            output_data=output_data,
        )
        if next_status in TERMINAL_AGENT_RUN_STATUSES:
            model.finished_at = datetime.now(timezone.utc)
        await self.session.flush()
        return AgentRunFinishResult(
            status=next_status,
            conversation_status=resolved_conversation_status,
            updated=True,
        )

    async def _update_conversation_status(
        self,
        *,
        conversation_id: UUID,
        status: ConversationStatus,
        output_data: JsonValue | None = None,
    ) -> None:
        conversation = await self.session.get(ConversationModel, conversation_id)
        if conversation is None:
            return
        conversation.status = status.value
        if output_data is not None or status == ConversationStatus.RUNNING:
            conversation.output_data = output_data
