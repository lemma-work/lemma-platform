"""Reading and writing the runtime profiles an organization can run on."""

from __future__ import annotations

from uuid import UUID

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.core.log.log import get_logger
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.errors import (
    OrganizationDefaultConflictError,
    RuntimeProfileConflictError,
    RuntimeProfileNameTakenError,
    RuntimeProfileNotFoundError,
    RuntimeProfileUnreadableError,
)
from app.modules.agent.domain.runtime_profiles import (
    AgentRuntimeProfile,
    RuntimeProfileProtocol,
    RuntimeProfileScope,
    RuntimeCredentials,
    RuntimeProfileStatus,
    reveal_credentials,
)
from app.modules.agent.infrastructure.runtime_models import (
    AgentRuntimeProfileModel,
)
from app.modules.connectors.contracts import SecretEncryptionPort
from app.modules.identity.contracts.profiles import user_profile


logger = get_logger(__name__)

#: The index that keeps at most one organization default (``runtime_models``).
_ORGANIZATION_DEFAULT_INDEX = "uq_agent_runtime_profile_org_default"

_CREDENTIALS: TypeAdapter[RuntimeCredentials | None] = TypeAdapter(
    RuntimeCredentials | None
)


def _error_locations(error: ValidationError) -> list[str]:
    return [".".join(str(part) for part in item["loc"]) for item in error.errors()]


def _log_unreadable(
    instance: AgentRuntimeProfileModel, error: ValidationError, *, organization_id: UUID
) -> None:
    # Where it failed, never what it read: a validation error's message and
    # traceback quote the input, and for this row the input can be a
    # decrypted key.
    logger.warning(
        "agent.runtime_profile.unreadable.skipped",
        profile_id=str(instance.id),
        organization_id=str(organization_id),
        error_type=type(error).__name__,
        error_locations=_error_locations(error),
    )


# Rows written for the retired local daemon still carry its per-tool protocols
# (CODEX_APP_SERVER, CLAUDE_CODE, …). They can never execute again, and
# RuntimeProfileProtocol no longer parses them, so every read filters on the
# protocols still understood rather than letting one stale row fail the query
# for the whole organization.
_KNOWN_RUNTIME_PROFILE_PROTOCOLS = [
    protocol.value for protocol in RuntimeProfileProtocol
]


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
        # The row is validated before the decrypted key is anywhere near it, so
        # a row that fails validation fails with nothing secret in its error.
        # The key is then checked on its own and attached.
        entity = AgentRuntimeProfile.model_validate(
            instance.to_entity().model_dump(mode="json", exclude={"credentials"})
        )
        decrypted = self.encryption.decrypt_json(instance.credentials)
        credentials = (
            _CREDENTIALS.validate_python(decrypted) if decrypted is not None else None
        )
        return entity.model_copy(update={"credentials": credentials})

    def _readable(self, instance: AgentRuntimeProfileModel) -> AgentRuntimeProfile:
        """One row as an entity, or the typed refusal when it no longer validates.

        For reads that name a single profile. The listing skips such a row
        instead (`get_visible`), because there one row must not cost the rest.
        """
        try:
            return self._to_entity(instance)
        except ValidationError as error:
            _log_unreadable(instance, error, organization_id=instance.organization_id)
            raise RuntimeProfileUnreadableError() from None

    def _apply_entity(
        self,
        instance: AgentRuntimeProfileModel,
        entity: AgentRuntimeProfile,
    ) -> AgentRuntimeProfileModel:
        """Write the columns an edit may change.

        Identity - organization, user, scope, kind, runtime_type, harness_id -
        is deliberately absent. runtime_type and harness_id are bound together
        by ck_agent_runtime_profile_harness_binding, and changing scope would
        move the row between the two partial unique indexes, so both belong to
        creation only.
        """
        instance.name = entity.name
        instance.description = entity.description
        instance.default_model_name = entity.default_model_name
        # Fresh containers rather than in-place mutation, so SQLAlchemy sees the
        # JSONB attributes as dirty and actually writes them.
        instance.model_catalog = [
            item.model_dump(mode="json") for item in entity.model_catalog
        ]
        instance.config = self._serialize_json(entity.config) or {}
        # reveal_credentials (not _serialize_json) so SecretStr fields persist
        # as their plaintext before encryption — model_dump(mode="json") would
        # otherwise store the masked "**********" and lose the real key.
        instance.credentials = self.encryption.encrypt_json(
            reveal_credentials(entity.credentials)
        )
        instance.status = entity.status.value
        instance.profile_metadata = dict(entity.metadata)
        return instance

    def _to_model(self, entity: AgentRuntimeProfile) -> AgentRuntimeProfileModel:
        return self._apply_entity(
            AgentRuntimeProfileModel(
                # The id the caller built the entity with is the row's id: one
                # assigned here instead would leave the caller holding an id
                # that names nothing.
                id=UUID(entity.id),
                organization_id=entity.organization_id,
                user_id=entity.user_id,
                harness_id=entity.harness_id,
                # runtime_type mirrors kind: the harness-binding check constraint
                # is expressed over runtime_type, not kind.
                runtime_type=entity.kind.value,
                scope=entity.scope.value,
                kind=entity.kind.value,
                protocol=entity.protocol.value,
            ),
            entity,
        )

    async def create(self, entity: AgentRuntimeProfile) -> AgentRuntimeProfile:
        instance = self._to_model(entity)
        self.session.add(instance)
        await self._flush_unique(entity.name, profile_id=None)
        return self._to_entity(instance)

    async def _flush_unique(self, name: str, *, profile_id: str | None) -> None:
        """Flush, mapping a name collision and a lost race to their 409s."""
        try:
            await self.session.flush()
        except IntegrityError as exc:
            if _ORGANIZATION_DEFAULT_INDEX in str(exc.orig):
                raise OrganizationDefaultConflictError() from exc
            raise RuntimeProfileNameTakenError(name) from exc
        except StaleDataError as exc:
            # The UPDATE matched no row at the version this edit read: another
            # write committed in between.
            logger.warning(
                "agent.runtime_profile.concurrent_edit.refused",
                profile_id=profile_id,
            )
            raise RuntimeProfileConflictError() from exc

    async def update(self, entity: AgentRuntimeProfile) -> AgentRuntimeProfile:
        """Write ``entity`` over its row, unless the row moved since it was read.

        ``entity.version`` is the version its read saw. A row already past it
        is refused here; one that moves between this check and the flush is
        refused by the mapper's ``version_id_col``. Either way the caller gets
        `RuntimeProfileConflictError`, never a silent overwrite.
        """
        instance = await self._load(profile_id=entity.id, include_disabled=True)
        if instance is None:
            raise RuntimeProfileNotFoundError()
        if instance.version != entity.version:
            logger.warning(
                "agent.runtime_profile.concurrent_edit.refused",
                profile_id=entity.id,
            )
            raise RuntimeProfileConflictError()
        self._apply_entity(instance, entity)
        await self._flush_unique(entity.name, profile_id=entity.id)
        return self._to_entity(instance)

    async def set_status(
        self,
        *,
        profile_id: str,
        organization_id: UUID,
        user_id: UUID,
        status: RuntimeProfileStatus,
    ) -> AgentRuntimeProfile | None:
        instance = await self._load(
            profile_id=profile_id,
            organization_id=organization_id,
            user_id=user_id,
            # An already-archived profile has to be loadable, or it could never
            # be restored.
            include_disabled=True,
        )
        if instance is None:
            return None
        instance.status = status.value
        # Restoring can collide: another profile may have taken this name while
        # this one sat archived.
        await self._flush_unique(instance.name, profile_id=profile_id)
        return self._readable(instance)

    async def _load(
        self,
        *,
        profile_id: str,
        organization_id: UUID | None = None,
        user_id: UUID | None = None,
        include_disabled: bool = False,
    ) -> AgentRuntimeProfileModel | None:
        try:
            profile_uuid = UUID(profile_id)
        except ValueError:
            return None
        stmt = select(AgentRuntimeProfileModel).where(
            AgentRuntimeProfileModel.id == profile_uuid,
            AgentRuntimeProfileModel.protocol.in_(_KNOWN_RUNTIME_PROFILE_PROTOCOLS),
        )
        if organization_id is not None:
            stmt = stmt.where(
                AgentRuntimeProfileModel.organization_id == organization_id
            )
        if user_id is not None:
            stmt = stmt.where(
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
                )
            )
        if not include_disabled:
            stmt = stmt.where(
                AgentRuntimeProfileModel.status == RuntimeProfileStatus.ACTIVE.value
            )
        result = await self.session.execute(stmt.limit(1))
        return result.scalar_one_or_none()

    async def get_personal_by_id(
        self, *, profile_id: str, organization_id: UUID
    ) -> AgentRuntimeProfile | None:
        """A member's active personal profile, whoever is asking.

        Only for saying *whose* it is when someone else's turn reaches it; it
        must never be what a run resolves to.
        """
        instance = await self._load(
            profile_id=profile_id, organization_id=organization_id
        )
        if instance is None or instance.scope != RuntimeProfileScope.PERSONAL.value:
            return None
        return self._readable(instance)

    async def owner_first_name(self, user_id: UUID) -> str | None:
        """The name a teammate knows this profile's owner by, when there is one."""
        profile = await user_profile(self.session, user_id)
        if profile is None:
            return None
        return (profile.first_name or "").strip() or None

    async def get_visible(
        self,
        *,
        organization_id: UUID,
        user_id: UUID,
        include_disabled: bool = False,
    ) -> list[AgentRuntimeProfile]:
        stmt = select(AgentRuntimeProfileModel).where(
            AgentRuntimeProfileModel.organization_id == organization_id,
            AgentRuntimeProfileModel.protocol.in_(_KNOWN_RUNTIME_PROFILE_PROTOCOLS),
            (AgentRuntimeProfileModel.scope == RuntimeProfileScope.ORGANIZATION.value)
            | (
                (AgentRuntimeProfileModel.scope == RuntimeProfileScope.PERSONAL.value)
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
        # One unmappable row must not blank the workspace's entire model list.
        # The protocol filter above already excludes retired daemon profiles, so
        # anything reaching here has a protocol this build knows and a body it
        # could not validate - a partial write, a hand-edited row, or a field
        # this build tightened. Skipping it costs that one profile; raising
        # costs every profile the organization has, and the Models page with it.
        profiles: list[AgentRuntimeProfile] = []
        for instance in result.scalars():
            try:
                profiles.append(self._to_entity(instance))
            except ValidationError as error:
                _log_unreadable(instance, error, organization_id=organization_id)
        return profiles

    async def get_visible_by_id(
        self,
        *,
        profile_id: str,
        organization_id: UUID,
        user_id: UUID,
        include_disabled: bool = False,
    ) -> AgentRuntimeProfile | None:
        instance = await self._load(
            profile_id=profile_id,
            organization_id=organization_id,
            user_id=user_id,
            include_disabled=include_disabled,
        )
        return self._readable(instance) if instance else None
