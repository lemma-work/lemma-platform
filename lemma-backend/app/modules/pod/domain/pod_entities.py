from datetime import date, datetime, timezone
from enum import Enum
from typing import Literal, Mapping, Optional
from uuid import UUID
from pydantic import (
    BaseModel,
    Field,
    ValidationError,
    ValidatorFunctionWrapHandler,
    field_validator,
    model_serializer,
    model_validator,
)
from app.core.domain.aggregate import AggregateRoot
from app.core.domain.runtime import AgentRuntimeConfig
from app.core.log.log import get_logger
from app.modules.identity.contracts import OrganizationRole, UserEntity
from app.modules.pod.domain.roles import PodRole

logger = get_logger(__name__)


class PodJoinPolicy(str, Enum):
    """Who may self-join a pod, ordered from closed to open."""

    INVITE_ONLY = "INVITE_ONLY"  # default — invite or approved join-request only
    ORG_MEMBERS = "ORG_MEMBERS"  # any member of the pod's org may self-join
    PUBLIC = "PUBLIC"  # any Lemma user may self-join (auto-added to the org)


class PodCreationSource(str, Enum):
    """Whether somebody asked for a pod, or the platform made it for them.

    Carried on ``PodCreatedEvent`` so a count of pods people chose to build is
    not also a count of people who signed up: joining an organization
    provisions a personal workspace whether or not anyone wanted one.
    """

    PERSON = "person"
    PROVISIONED = "provisioned"


class PodRecipe(BaseModel):
    """A record of a bundle installed into this pod (the durable trace of an
    import; the ephemeral import job state is not kept). ``kind`` distinguishes an
    uploaded bundle from a GitHub-sourced one; ``repo_url`` is set for GitHub."""

    kind: str  # "upload" | "github"
    name: str | None = None
    repo_url: str | None = None
    format_version: int | None = None
    imported_at: datetime
    imported_by: UUID


TriggerWriteModeSetting = Literal["skip", "each", "batch"]


class PodTriggerSettings(BaseModel):
    """Pod-wide defaults for the pod's DATASTORE triggers."""

    bulk_writes_default: TriggerWriteModeSetting | None = Field(
        default=None,
        description=(
            "What a bulk write does to this pod's triggers that do not say: "
            "skip (recorded as skipped), each (fire per matching row) or batch "
            "(one fire with the rows). Omitted means skip."
        ),
    )


class PodContactSettings(BaseModel):
    """How the pod treats the people outside it who write to it."""

    auto_acknowledge: str | None = Field(
        default=None,
        min_length=1,
        max_length=500,
        description=(
            "Said once to a contact who writes while their channel's agents are "
            'off -- for example "Thanks, someone from {pod} will reply" -- '
            "until a member writes to them or their channel is closed. "
            "``{pod}`` is the pod's name. Omitted means nothing is said."
        ),
    )


class OutcomeKind(str, Enum):
    """The two shapes of what a pod owns.

    ``week`` is a bar held every week, which never finishes ("Every customer
    hears back within a day"). ``date`` is a finish line, reached once by its
    ``due`` date ("The launch ships by the date it is due"). The values are the plan
    file's own (``kind: week`` in ``/plan.md``), so a plan a person or the
    pod's agent wrote reads straight across.
    """

    WEEK = "week"
    DATE = "date"


#: Where the plan for what a pod owns is kept: the prose people and the pod's
#: agent both edit -- what must be true, how the work is done, who holds what,
#: what never happens -- beside the structured part on the pod's config.
PLAN_PATH = "/plan.md"

#: A measure is one query; anything near this long is several pasted together.
MEASURE_MAX_CHARS = 4000

#: The unit that makes a measure a share: it answers 0 to 100, as its target is
#: written ("90" with "%" is "90%"), never a 0-1 fraction.
SHARE_UNIT = "%"


class OutcomeInput(BaseModel):
    """What the pod owns, as a person or its agent sets it.

    The fields are the front matter of the plan file the workspace keeps at
    ``/plan.md`` -- ``outcome`` (here ``sentence``), ``kind``, ``target``,
    ``unit``, ``due`` and ``measure`` -- held here so there is one copy of
    them. The plan's prose (what must be true, how the work is done, who holds
    what, what never happens) stays in the file.
    """

    sentence: str = Field(
        min_length=3,
        max_length=200,
        description="What it owns, as one sentence a person can check.",
    )
    kind: OutcomeKind = Field(
        description="week: a bar held every week. date: a finish line by `due`."
    )
    target: float | None = Field(
        default=None,
        allow_inf_nan=False,
        description=(
            "The number the measure is held to: 90 with unit '%' for a share, "
            "22 for a finish line of 22 steps. Unset while it is still being "
            "worked out."
        ),
    )
    unit: str | None = Field(
        default=None,
        max_length=24,
        description=(
            "'%' when the measure answers a share from 0 to 100; otherwise what "
            "it counts, such as 'steps' or 'posts'."
        ),
    )
    due: date | None = Field(
        default=None,
        description="The finish line's date. Required for kind=date, refused for week.",
    )
    measure: str | None = Field(
        default=None,
        max_length=MEASURE_MAX_CHARS,
        description=(
            "One read-only SQL query over the pod's tables that answers a "
            "single number: one row, one column. Counted for every member alike, "
            "so only someone who can read every row it touches may set it."
        ),
    )

    @field_validator("sentence")
    @classmethod
    def _one_line(cls, value: str) -> str:
        said = " ".join(value.split())
        if len(said) < 3:
            raise ValueError("say what it owns in a sentence")
        return said

    @field_validator("unit", "measure")
    @classmethod
    def _blank_is_unset(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("unit")
    @classmethod
    def _unit_is_a_word(cls, value: str | None) -> str | None:
        if value is not None and any(char in value for char in "\r\n"):
            raise ValueError("a unit is one short word, such as '%' or 'steps'")
        return value

    @model_validator(mode="after")
    def _shape_fits_kind(self) -> "OutcomeInput":
        if self.kind is OutcomeKind.DATE and self.due is None:
            raise ValueError("a finish line needs its due date")
        if self.kind is OutcomeKind.WEEK and self.due is not None:
            raise ValueError("a bar held every week has no due date")
        if (
            self.unit == SHARE_UNIT
            and self.target is not None
            and not 0 <= self.target <= 100
        ):
            raise ValueError("a share's target is from 0 to 100, as in 90 for 90%")
        return self


class PodOutcome(OutcomeInput):
    """What the pod owns, as stored on its config."""

    since: date | None = Field(
        default=None,
        description=(
            "When the pod took this on: set by the server when it is first "
            "set, and kept while only its wording, target or measure change."
        ),
    )
    row_security_checked: list[UUID] = Field(
        default_factory=list,
        description=(
            "The tables with row security the measure counts that were checked "
            "when it was set, by id: whoever set it administers each. Set by "
            "the server. A measure that counts a table with row security not "
            "listed here is not read until someone who administers that table "
            "sets it again."
        ),
    )


class PodConfig(BaseModel):
    """Typed pod-level configuration."""

    # ``default_profile_id`` is the legacy provider-only default (no model). It is
    # kept for backward compatibility — old pods, old clients, and any code still
    # reading the raw key — and is mirrored from ``default_runtime`` on write.
    # New code should set/read ``default_runtime`` (profile + optional model).
    default_profile_id: str | None = Field(default=None, min_length=1)
    default_runtime: AgentRuntimeConfig | None = None
    join_policy: PodJoinPolicy = PodJoinPolicy.INVITE_ONLY
    # Bundles installed into this pod, appended on each successful import. Omitted
    # from the serialized config when empty so legacy config blobs are unchanged.
    recipes: list[PodRecipe] = Field(default_factory=list)
    triggers: PodTriggerSettings | None = None
    contacts: PodContactSettings | None = None
    #: What the pod owns. Omitted until somebody sets one, and set only through
    #: ``PodService.set_outcome``, which checks a measure before keeping it: a
    #: config written any other way keeps the outcome it had.
    outcome: PodOutcome | None = None

    @field_validator("outcome", mode="wrap")
    @classmethod
    def _unreadable_outcome_is_none(
        cls, value: object, handler: ValidatorFunctionWrapHandler
    ) -> "PodOutcome | None":
        """A stored outcome that no longer reads as one is no outcome.

        Without this, ``from_raw`` would answer a default config for the whole
        pod -- join policy, runtime and recipes with it -- because one field
        written under an older shape stopped validating.
        """
        try:
            return handler(value)
        except ValidationError:
            logger.warning("pod.config.outcome_unreadable.degraded", exc_info=True)
            return None

    @field_validator("default_profile_id")
    @classmethod
    def normalize_default_profile_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        profile_id = value.strip()
        if not profile_id:
            raise ValueError("default_profile_id cannot be empty")
        return profile_id

    @classmethod
    def from_raw(cls, config: object) -> "PodConfig":
        """Parse a raw stored config blob, tolerating legacy/malformed shapes."""
        if isinstance(config, Mapping):
            try:
                return cls.model_validate(dict(config))
            except ValidationError:
                return cls()
        return cls()

    def resolved_default_runtime(self) -> AgentRuntimeConfig | None:
        """The pod's default runtime, preferring the full ``default_runtime``.

        Falls back to the legacy ``default_profile_id`` (model unset == use the
        profile's own default model), so old pods resolve exactly as before.
        Returns ``None`` when the pod pins no default.
        """
        if self.default_runtime is not None:
            return self.default_runtime
        if self.default_profile_id:
            return AgentRuntimeConfig(profile_id=self.default_profile_id)
        return None

    @model_serializer(mode="wrap")
    def serialize_without_empty_defaults(self, handler):
        data = handler(self)
        if data.get("default_profile_id") is None:
            data.pop("default_profile_id", None)
        if data.get("default_runtime") is None:
            data.pop("default_runtime", None)
        if not data.get("recipes"):
            data.pop("recipes", None)
        if data.get("triggers") is None:
            data.pop("triggers", None)
        if data.get("contacts") is None:
            data.pop("contacts", None)
        if data.get("outcome") is None:
            data.pop("outcome", None)
        return data


class PodEntity(AggregateRoot):
    """Pod entity."""

    user_id: UUID
    organization_id: UUID
    name: str
    description: Optional[str] = None
    icon_url: Optional[str] = None
    config: PodConfig = Field(default_factory=PodConfig)
    is_deleted: bool = False

    def mark_created(
        self,
        creator_id: UUID,
        *,
        source: PodCreationSource = PodCreationSource.PERSON,
    ) -> None:
        """Add pod created event to aggregate."""
        from app.modules.pod.domain.events import PodCreatedEvent

        self.add_event(
            PodCreatedEvent(
                pod_id=self.id,
                organization_id=self.organization_id,
                creator_id=creator_id,
                name=self.name,
                source=source.value,
            )
        )

    def mark_deleted(self) -> None:
        """Soft delete aggregate and emit deletion event for downstream cleanup."""
        from app.core.authorization.current import get_current_context
        from app.modules.pod.domain.events import PodDeletedEvent

        actor = get_current_context()
        self.is_deleted = True
        self.add_event(
            PodDeletedEvent(
                pod_id=self.id,
                organization_id=self.organization_id,
                deleted_by_user_id=getattr(actor, "user_id", None),
                pod_created_at=self.created_at,
            )
        )


class PodUpdateEntity(BaseModel):
    """Pod update entity."""

    name: Optional[str] = None
    description: Optional[str] = None
    icon_url: Optional[str] = None
    config: PodConfig | None = None


class PodMemberEntity(AggregateRoot):
    """Pod member entity."""

    pod_id: UUID
    organization_member_id: UUID
    roles: list[str] = Field(default_factory=list)
    user_id: UUID | None = None
    user_email: str | None = None
    user_name: str | None = None
    user: UserEntity | None = None

    @property
    def pod_member_id(self) -> UUID:
        return self.id

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_role_input(cls, data):
        if isinstance(data, dict) and not data.get("roles") and data.get("role"):
            role = data.get("role")
            data = dict(data)
            data["roles"] = [role.value if isinstance(role, PodRole) else str(role)]
        return data

    @property
    def role(self) -> PodRole:
        from app.modules.pod.domain.visibility import highest_role, normalize_role_list

        normalized = normalize_role_list(self.roles)
        if not normalized:
            return PodRole.USER
        return PodRole(highest_role(normalized))

    @property
    def email(self) -> str | None:
        return self.user_email

    def assign_role(self, role: PodRole) -> None:
        self.roles = [role.value]

    def assign_roles(self, roles: list[str]) -> None:
        from app.modules.pod.domain.visibility import normalize_role_list

        self.roles = normalize_role_list(roles)

    def mark_added(
        self,
        *,
        user_id: UUID,
        email: str,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> None:
        from app.modules.pod.domain.events import PodMemberAddedEvent

        self.add_event(
            PodMemberAddedEvent(
                pod_id=self.pod_id,
                user_id=user_id,
                role=self.role.value,
                email=email,
                first_name=first_name,
                last_name=last_name,
            )
        )

    def mark_removed(self, *, user_id: UUID) -> None:
        from app.modules.pod.domain.events import PodMemberRemovedEvent

        self.add_event(
            PodMemberRemovedEvent(
                pod_id=self.pod_id,
                user_id=user_id,
            )
        )


class PodJoinRequestStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class PodJoinRequestEntity(AggregateRoot):
    pod_id: UUID
    organization_id: UUID
    user_id: UUID
    status: PodJoinRequestStatus = PodJoinRequestStatus.PENDING
    requested_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    approved_at: datetime | None = None
    approved_by_user_id: UUID | None = None
    org_role: OrganizationRole | None = None
    pod_role: PodRole | None = None
    # Display-only — populated from the users table on read, never persisted here.
    user_email: str | None = None
    user_name: str | None = None

    def mark_requested(self) -> None:
        """Emit join-requested event so pod admins can be notified."""
        from app.modules.pod.domain.events import PodJoinRequestedEvent

        self.add_event(
            PodJoinRequestedEvent(
                pod_id=self.pod_id,
                organization_id=self.organization_id,
                requester_user_id=self.user_id,
                join_request_id=self.id,
            )
        )

    def mark_approved(
        self,
        *,
        approved_by_user_id: UUID,
        approved_at: datetime | None = None,
        org_role: OrganizationRole,
        pod_role: PodRole,
    ) -> None:
        self.status = PodJoinRequestStatus.APPROVED
        self.approved_by_user_id = approved_by_user_id
        self.approved_at = approved_at or datetime.now(timezone.utc)
        self.org_role = org_role
        self.pod_role = pod_role
