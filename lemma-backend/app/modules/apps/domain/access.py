"""Credentials for reading one app's build, never for calling the pod API."""

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel

from app.core.domain.errors import DomainError
from app.modules.apps.domain.errors import AppNotFoundError


class AppAccessRequiredError(AppNotFoundError):
    """A host needs authentication before revealing whether its app exists."""


class AppAccessErrorCode(StrEnum):
    INVALID = "APP_ACCESS_INVALID"
    UNAVAILABLE = "APP_ACCESS_UNAVAILABLE"
    RATE_LIMITED = "APP_ACCESS_RATE_LIMITED"


class AppAccessInvalidError(DomainError):
    def __init__(self) -> None:
        super().__init__(
            "App access expired or could not be verified. Try again.",
            code=AppAccessErrorCode.INVALID,
            status_code=401,
        )


class AppAccessUnavailableError(DomainError):
    def __init__(self) -> None:
        super().__init__(
            "App access is temporarily unavailable. Try again.",
            code=AppAccessErrorCode.UNAVAILABLE,
            status_code=503,
        )


class AppAccessRateLimitedError(DomainError):
    def __init__(self) -> None:
        super().__init__(
            "Too many app access requests. Try again shortly.",
            code=AppAccessErrorCode.RATE_LIMITED,
            status_code=429,
        )


class AppAccessRequest(BaseModel):
    origin: str
    slug: str
    release_ref: str | None
    challenge: str
    binding_hash: str


class AppAccessTarget(BaseModel):
    app_id: UUID
    pod_id: UUID
    name: str


class AppAccessSession(AppAccessTarget):
    user_id: UUID
    origin: str
    slug: str
    release_ref: str | None
    parent_handle: str
    expires_at: int


class AppAccessCode(BaseModel):
    request_id: str
    session: AppAccessSession
