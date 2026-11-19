from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import (
    BaseModel,
    Field,
    ValidationError,
    ValidatorFunctionWrapHandler,
    field_validator,
)

from app.core.log.log import get_logger

logger = get_logger(__name__)

#: The longest "something else" a person may type about what they do.
WORK_OTHER_MAX_CHARS = 120


class Job(StrEnum):
    """What a person does, as the first-run question offers it."""

    SALES = "SALES"
    MARKETING = "MARKETING"
    PRODUCT = "PRODUCT"
    FOUNDER = "FOUNDER"
    OPERATIONS = "OPERATIONS"
    HR = "HR"
    PROJECT_MANAGEMENT = "PROJECT_MANAGEMENT"
    ENGINEERING = "ENGINEERING"
    SUPPORT = "SUPPORT"


class WorkAnswer(BaseModel):
    """What a person said they do, the one time they were asked.

    An answer with no jobs and nothing typed is a skip, and is kept as one: it
    still says when they were asked, which is what stops them being asked again.
    """

    jobs: list[Job] = Field(default_factory=list)
    other: str | None = Field(default=None, max_length=WORK_OTHER_MAX_CHARS)
    answered_at: datetime

    @field_validator("jobs")
    @classmethod
    def _each_job_once(cls, jobs: list[Job]) -> list[Job]:
        return list(dict.fromkeys(jobs))

    @field_validator("other", mode="before")
    @classmethod
    def _blank_is_nothing(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value


class UserPreferences(BaseModel):
    """Typed container for a user's cross-cutting preferences (stored as JSONB
    on ``users.preferences``).

    ``default_surfaces`` maps a surface platform value (e.g. ``"WHATSAPP"``) to
    the surface id the user prefers when the same external identity resolves to
    several pods — e.g. a person reachable via the shared system bot/number who
    belongs to pods in more than one organization. Absent platform → no default.

    ``home_seen`` maps an organization id to when the user last marked its Home
    seen, so Home can say what moved since on any browser or device. Written
    only by ``infrastructure/organization_home_seen``, one key at a time; it is
    declared here so every other writer of preferences, which round-trips this
    model, keeps it.

    ``work`` is what the person said they do when first asked: kept so they
    are asked once, and so what a Lemma offers them can start from it.
    """

    default_surfaces: dict[str, UUID] = Field(default_factory=dict)
    home_seen: dict[UUID, datetime] = Field(default_factory=dict)
    #: Written only by ``infrastructure/user_work``, one key, like ``home_seen``.
    work: WorkAnswer | None = None

    @field_validator("work", mode="wrap")
    @classmethod
    def _unreadable_work_is_unanswered(
        cls, value: object, handler: ValidatorFunctionWrapHandler
    ) -> WorkAnswer | None:
        """A stored answer that no longer reads as one is no answer.

        Preferences are read with every user, so an answer stored under an
        older shape -- a job since retired -- must not make the whole account
        unreadable. The person is asked again instead.
        """
        try:
            return handler(value)
        except ValidationError:
            logger.warning("identity.user_preferences.work_unreadable.degraded")
            return None

    def default_surface_for(self, platform: str) -> UUID | None:
        return self.default_surfaces.get(str(platform).upper())

    def with_default_surface(
        self, platform: str, surface_id: UUID
    ) -> "UserPreferences":
        """Return a copy with ``platform``'s default set to ``surface_id``."""
        merged = dict(self.default_surfaces)
        merged[str(platform).upper()] = surface_id
        return self.model_copy(update={"default_surfaces": merged})

    def without_default_surface(self, platform: str) -> "UserPreferences":
        """Return a copy with ``platform``'s default cleared (if present)."""
        merged = dict(self.default_surfaces)
        merged.pop(str(platform).upper(), None)
        return self.model_copy(update={"default_surfaces": merged})
