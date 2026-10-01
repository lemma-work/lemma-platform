"""Reading and writing the runtime profiles an organization can run on.

A profile's credentials and its header values (an ``Authorization`` header is
as much a key as ``api_key``) live in one vault secret, never in its columns.
``config`` keeps every other setting; its ``headers`` are lifted out on write
and put back on read, so the entity callers see has the same shape it always
had.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select

from app.core.log.log import get_logger
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.runtime_profiles import (
    AgentRuntimeProfile,
    RuntimeProfileProtocol,
    RuntimeProfileScope,
    RuntimeProfileStatus,
    reveal_credentials,
)
from app.modules.agent.infrastructure.runtime_models import (
    AgentRuntimeProfileModel,
)
from app.modules.vault.contracts import (
    JsonObject,
    JsonValue,
    SecretScope,
    Vault,
    vault_for,
)


logger = get_logger(__name__)

SECRETS_PURPOSE = "agent.runtime_profile.secrets"
_OWNER_TABLE = "agent_runtime_profiles"

# Rows written for the retired local daemon still carry its per-tool protocols
# (CODEX_APP_SERVER, CLAUDE_CODE, …). They can never execute again, and
# RuntimeProfileProtocol no longer parses them, so every read filters on the
# protocols still understood rather than letting one stale row fail the query
# for the whole organization.
_KNOWN_RUNTIME_PROFILE_PROTOCOLS = [
    protocol.value for protocol in RuntimeProfileProtocol
]


def _secret_scope(instance: AgentRuntimeProfileModel) -> SecretScope:
    # The organization only: user_id is SET NULL when the user goes, and a
    # scope that changed underneath the ciphertext would no longer open it.
    return SecretScope(organization_id=instance.organization_id)


def _split_headers(config: JsonObject) -> tuple[JsonObject, JsonObject]:
    """``config`` without its header values, and the header values."""
    headers = config.get("headers")
    if not isinstance(headers, dict) or not headers:
        return config, {}
    return {key: value for key, value in config.items() if key != "headers"}, headers


def _secrets_value(
    credentials: Mapping[str, object] | None, headers: JsonObject | None
) -> JsonObject:
    return {
        "credentials": cast("JsonValue", dict(credentials) if credentials else None),
        "headers": headers or {},
    }


async def put_profile_secrets(
    vault: Vault,
    *,
    organization_id: UUID | None,
    credentials: Mapping[str, object] | None,
    headers: JsonObject | None = None,
) -> UUID:
    """Store a new profile's secrets; the id belongs in ``secrets_secret_id``.

    The repository's own write, and the one for rows made outside it (test
    fixtures, seeding), so both store the same shape under the same binding.
    """
    ref = await vault.put(
        scope=SecretScope(organization_id=organization_id),
        purpose=SECRETS_PURPOSE,
        value=_secrets_value(credentials, headers),
        owner_table=_OWNER_TABLE,
    )
    return ref.id


class AgentRuntimeProfileRepository:
    """Repository for organization-owned runtime profiles."""

    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        vault: Vault | None = None,
    ):
        self.uow = uow
        self.session = uow.session
        self._vault = vault or vault_for(uow)

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

    @staticmethod
    def _to_entity(
        instance: AgentRuntimeProfileModel, secrets: JsonObject | None
    ) -> AgentRuntimeProfile:
        data = instance.to_entity().model_dump(mode="json")
        secrets = secrets or {}
        data["credentials"] = secrets.get("credentials")
        headers = secrets.get("headers")
        if headers and isinstance(data["config"], dict):
            data["config"] = {**data["config"], "headers": headers}
        return AgentRuntimeProfile.model_validate(data)

    async def _read(self, instance: AgentRuntimeProfileModel) -> AgentRuntimeProfile:
        secrets: JsonObject | None = None
        if instance.secrets_secret_id is not None:
            revealed = await self._vault.reveal(
                instance.secrets_secret_id,
                expect=_secret_scope(instance),
                purpose=SECRETS_PURPOSE,
            )
            secrets = revealed.json()
        return self._to_entity(instance, secrets)

    async def _read_many(
        self, instances: list[AgentRuntimeProfileModel]
    ) -> dict[UUID, JsonObject]:
        """Every listed profile's secrets by profile id, in one vault query."""
        owners = {
            instance.secrets_secret_id: instance
            for instance in instances
            if instance.secrets_secret_id is not None
        }
        revealed = await self._vault.reveal_many(
            {secret_id: _secret_scope(owner) for secret_id, owner in owners.items()},
            purpose=SECRETS_PURPOSE,
        )
        return {
            owners[secret_id].id: item.json() for secret_id, item in revealed.items()
        }

    async def _write_secrets(
        self, instance: AgentRuntimeProfileModel, entity: AgentRuntimeProfile
    ) -> JsonObject:
        """Store the entity's secrets; return the config left to store in plain.

        Runs before any column is assigned: the vault write executes at once,
        and an autoflush of a half-edited row there would surface a name
        collision outside ``_flush_unique``.
        """
        config, headers = _split_headers(self._serialize_json(entity.config) or {})
        # reveal_credentials (not _serialize_json) so SecretStr fields persist
        # as their plaintext - model_dump(mode="json") would otherwise store the
        # masked "**********" and lose the real key.
        credentials = reveal_credentials(entity.credentials)
        if not credentials and not headers:
            # Repointing to NULL deletes the old secret (vault_owned trigger).
            instance.secrets_secret_id = None
            return config
        if instance.secrets_secret_id is None:
            instance.secrets_secret_id = await put_profile_secrets(
                self._vault,
                organization_id=instance.organization_id,
                credentials=credentials,
                headers=headers,
            )
        else:
            await self._vault.replace(
                instance.secrets_secret_id,
                expect=_secret_scope(instance),
                purpose=SECRETS_PURPOSE,
                value=_secrets_value(credentials, headers),
                skip_if_equal=True,
            )
        return config

    async def _apply_entity(
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
        config = await self._write_secrets(instance, entity)
        instance.name = entity.name
        instance.description = entity.description
        instance.default_model_name = entity.default_model_name
        # Fresh containers rather than in-place mutation, so SQLAlchemy sees the
        # JSONB attributes as dirty and actually writes them.
        instance.model_catalog = [
            item.model_dump(mode="json") for item in entity.model_catalog
        ]
        instance.config = dict(config)
        instance.status = entity.status.value
        instance.profile_metadata = dict(entity.metadata)
        return instance

    async def _to_model(self, entity: AgentRuntimeProfile) -> AgentRuntimeProfileModel:
        return await self._apply_entity(
            AgentRuntimeProfileModel(
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
        instance = await self._to_model(entity)
        self.session.add(instance)
        await self._flush_unique(entity.name)
        return await self._read(instance)

    async def _flush_unique(self, name: str) -> None:
        """Flush, mapping a name collision to the controller's 409."""
        from sqlalchemy.exc import IntegrityError

        try:
            await self.session.flush()
        except IntegrityError as exc:
            raise RuntimeError(
                f"Runtime profile named {name!r} already exists"
            ) from exc

    async def update(self, entity: AgentRuntimeProfile) -> AgentRuntimeProfile:
        instance = await self._load(profile_id=entity.id)
        if instance is None:
            raise RuntimeError(f"Runtime profile {entity.id!r} no longer exists")
        await self._apply_entity(instance, entity)
        await self._flush_unique(entity.name)
        return await self._read(instance)

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
        await self._flush_unique(instance.name)
        return await self._read(instance)

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
        instances = list((await self.session.execute(stmt)).scalars())
        secrets = await self._read_many(instances)
        # One unmappable row must not blank the workspace's entire model list.
        # The protocol filter above already excludes retired daemon profiles, so
        # anything reaching here has a protocol this build knows and a body it
        # could not validate - a partial write, a hand-edited row, or a field
        # this build tightened. Skipping it costs that one profile; raising
        # costs every profile the organization has, and the Models page with it.
        profiles: list[AgentRuntimeProfile] = []
        for instance in instances:
            try:
                profiles.append(self._to_entity(instance, secrets.get(instance.id)))
            except ValidationError as error:
                logger.warning(
                    "agent.runtime_profile.unreadable.skipped",
                    profile_id=str(instance.id),
                    organization_id=str(organization_id),
                    error=str(error),
                    exc_info=True,
                )
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
        return await self._read(instance) if instance else None
