"""Reading and writing the runtime profiles an organization can run on.

A profile's credentials and its header values (an ``Authorization`` header is
as much a key as ``api_key``) live in one vault secret, referenced by
``secrets_secret_id``. ``config`` keeps every other setting: its ``headers``
are lifted out on write and put back on read, so the entity callers see has
the shape it always had.

Until the contract migration, a row whose ``secrets_secret_id`` is empty was
written by the previous release during the rollout: its credentials are still
Fernet-encrypted in ``credentials`` and its headers are in ``config``. It is
read from there and moved into the vault by its next write.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast
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
from app.modules.identity.contracts.profiles import user_profile
from app.modules.vault.contracts import (
    JsonObject,
    JsonValue,
    SecretScope,
    SqlVault,
    legacy_json,
    vault_for,
)


logger = get_logger(__name__)

#: The index that keeps at most one organization default (``runtime_models``).
_ORGANIZATION_DEFAULT_INDEX = "uq_agent_runtime_profile_org_default"

_CREDENTIALS: TypeAdapter[RuntimeCredentials | None] = TypeAdapter(
    RuntimeCredentials | None
)

#: Bound into the ciphertext with the scope; must match migration 0078.
SECRETS_PURPOSE = "agent.runtime_profile.secrets"
_OWNER_TABLE = "agent_runtime_profiles"


class _Secrets:
    """A profile's credentials and header values, in plaintext. Never logged."""

    __slots__ = ("credentials", "headers")

    def __init__(
        self, credentials: Mapping[str, object] | None, headers: JsonObject | None
    ) -> None:
        self.credentials = dict(credentials) if credentials else None
        #: None: the headers are where ``config`` already has them.
        self.headers = headers

    def __repr__(self) -> str:
        return "_Secrets(***)"

    def vault_value(self) -> JsonObject:
        return {
            "credentials": cast("JsonValue", self.credentials),
            "headers": self.headers or {},
        }


def _secret_scope(instance: AgentRuntimeProfileModel) -> SecretScope:
    # The organization only, as the migration bound it: a personal profile's
    # owner is not part of the binding.
    return SecretScope(organization_id=instance.organization_id)


async def put_profile_secrets(
    vault: SqlVault,
    *,
    organization_id: UUID,
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
        value=_Secrets(credentials, headers).vault_value(),
        owner_table=_OWNER_TABLE,
    )
    return ref.id


def _split_headers(config: JsonObject) -> tuple[JsonObject, JsonObject]:
    """``config`` without its ``headers``, and the header values."""
    headers = config.get("headers")
    rest = {key: value for key, value in config.items() if key != "headers"}
    return rest, dict(headers) if isinstance(headers, dict) else {}


def _from_vault(value: JsonObject, config: Mapping[str, object]) -> _Secrets:
    credentials = value.get("credentials")
    headers = value.get("headers")
    return _Secrets(
        credentials if isinstance(credentials, dict) else None,
        # A ``headers`` key still in ``config`` is either the copy the
        # migration left beside the vault's (the same values) or one the
        # previous release wrote since (newer, and its trigger watches only
        # ``credentials``): either way it is the one to read. This release
        # never writes it.
        None
        if "headers" in config
        else (dict(headers) if isinstance(headers, dict) else {}),
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

    def __init__(self, uow: SqlAlchemyUnitOfWork, vault: SqlVault | None = None):
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
        instance: AgentRuntimeProfileModel, secrets: _Secrets
    ) -> AgentRuntimeProfile:
        # The row is validated before the decrypted key is anywhere near it, so
        # a row that fails validation fails with nothing secret in its error.
        # The key is then checked on its own and attached. Header values go in
        # with the config, whose validation restates errors without the input.
        data = instance.to_entity().model_dump(mode="json", exclude={"credentials"})
        if secrets.headers:
            data["config"] = {**data["config"], "headers": secrets.headers}
        entity = AgentRuntimeProfile.model_validate(data)
        credentials = (
            _CREDENTIALS.validate_python(secrets.credentials)
            if secrets.credentials is not None
            else None
        )
        return entity.model_copy(update={"credentials": credentials})

    async def _secrets(self, instance: AgentRuntimeProfileModel) -> _Secrets:
        if instance.secrets_secret_id is not None:
            revealed = await self._vault.reveal(
                instance.secrets_secret_id,
                expect=_secret_scope(instance),
                purpose=SECRETS_PURPOSE,
            )
            return _from_vault(revealed.json(), instance.config or {})
        # Written by the previous release during the rollout.
        return _Secrets(await legacy_json(instance.credentials), None)

    async def _secrets_of(
        self, instances: list[AgentRuntimeProfileModel]
    ) -> dict[UUID, _Secrets]:
        """Every listed profile's secrets by profile id, in one vault query.

        A profile whose secret is gone (a write by the previous release deleted
        it between the two reads) is left out.
        """
        owners = {
            instance.secrets_secret_id: instance
            for instance in instances
            if instance.secrets_secret_id is not None
        }
        revealed = await self._vault.reveal_many(
            {secret_id: _secret_scope(owner) for secret_id, owner in owners.items()},
            purpose=SECRETS_PURPOSE,
        )
        secrets: dict[UUID, _Secrets] = {}
        for instance in instances:
            if instance.secrets_secret_id is None:
                secrets[instance.id] = _Secrets(
                    await legacy_json(instance.credentials), None
                )
            elif (item := revealed.get(instance.secrets_secret_id)) is not None:
                secrets[instance.id] = _from_vault(item.json(), instance.config or {})
        return secrets

    async def _readable(
        self, instance: AgentRuntimeProfileModel
    ) -> AgentRuntimeProfile:
        """One row as an entity, or the typed refusal when it no longer validates.

        For reads that name a single profile. The listing skips such a row
        instead (`get_visible`), because there one row must not cost the rest.
        """
        secrets = await self._secrets(instance)
        try:
            return self._to_entity(instance, secrets)
        except ValidationError as error:
            _log_unreadable(instance, error, organization_id=instance.organization_id)
            raise RuntimeProfileUnreadableError() from None

    async def _write_secrets(
        self, instance: AgentRuntimeProfileModel, entity: AgentRuntimeProfile
    ) -> tuple[JsonObject, _Secrets]:
        """Store the entity's secrets in the vault; return the config left to
        store in plain, and what was stored.

        Runs before any column is assigned: the vault write executes at once,
        and an autoflush of a half-edited row there would surface a name
        collision outside ``_flush_unique``.
        """
        config, headers = _split_headers(self._serialize_json(entity.config) or {})
        # reveal_credentials (not _serialize_json) so SecretStr fields persist
        # as their plaintext -- model_dump(mode="json") would otherwise store
        # the masked "**********" and lose the real key.
        secrets = _Secrets(reveal_credentials(entity.credentials), headers)
        if not secrets.credentials and not headers:
            # Repointing to NULL deletes the old secret (`vault_owned`).
            instance.secrets_secret_id = None
        elif instance.secrets_secret_id is None:
            instance.secrets_secret_id = await put_profile_secrets(
                self._vault,
                organization_id=instance.organization_id,
                credentials=secrets.credentials,
                headers=headers,
            )
        else:
            await self._vault.replace(
                instance.secrets_secret_id,
                expect=_secret_scope(instance),
                purpose=SECRETS_PURPOSE,
                value=secrets.vault_value(),
                skip_if_equal=True,
            )
        # The previous release's column is emptied by every write, so a process
        # still on it fails closed instead of reading a stale key.
        instance.credentials = None
        return config, secrets

    async def _apply_entity(
        self,
        instance: AgentRuntimeProfileModel,
        entity: AgentRuntimeProfile,
    ) -> tuple[AgentRuntimeProfileModel, _Secrets]:
        """Write the columns an edit may change.

        Identity - organization, user, scope, kind, runtime_type, harness_id -
        is deliberately absent. runtime_type and harness_id are bound together
        by ck_agent_runtime_profile_harness_binding, and changing scope would
        move the row between the two partial unique indexes, so both belong to
        creation only.
        """
        config, secrets = await self._write_secrets(instance, entity)
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
        return instance, secrets

    async def _to_model(
        self, entity: AgentRuntimeProfile
    ) -> tuple[AgentRuntimeProfileModel, _Secrets]:
        return await self._apply_entity(
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
        instance, secrets = await self._to_model(entity)
        self.session.add(instance)
        await self._flush_unique(entity.name, profile_id=None)
        return self._to_entity(instance, secrets)

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
        _, secrets = await self._apply_entity(instance, entity)
        await self._flush_unique(entity.name, profile_id=entity.id)
        return self._to_entity(instance, secrets)

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
        return await self._readable(instance)

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
        return await self._readable(instance)

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
        instances = list((await self.session.execute(stmt)).scalars())
        secrets = await self._secrets_of(instances)
        # One unmappable row must not blank the workspace's entire model list.
        # The protocol filter above already excludes retired daemon profiles, so
        # anything reaching here has a protocol this build knows and a body it
        # could not validate - a partial write, a hand-edited row, or a field
        # this build tightened. Skipping it costs that one profile; raising
        # costs every profile the organization has, and the Models page with it.
        profiles: list[AgentRuntimeProfile] = []
        for instance in instances:
            found = secrets.get(instance.id)
            if found is None:
                continue
            try:
                profiles.append(self._to_entity(instance, found))
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
        return await self._readable(instance) if instance else None
