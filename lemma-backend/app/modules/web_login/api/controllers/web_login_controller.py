"""Seeing and removing what has been saved on your behalf.

A credential store nobody can inspect is one nobody can trust. These are the
routes that make a saved login a thing the person owns rather than a thing that
accumulates: what is saved, when it was last used, what has been done with it,
and how to take it away.

**Nothing here returns a secret**, at any privilege level, including to the
person who created it — the same promise `connector.auth_config.get` makes. The
listed shape has no field to put one in, which is why the promise is structural
rather than a rule somebody has to remember.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.core.api.dependencies import CurrentUser, UoWDep
from app.modules.web_login.domain.entities import WebLogin
from app.modules.web_login.infrastructure.repository import (
    WebLoginNotFound,
    WebLoginRepository,
)
from app.modules.web_login.services.origin import InvalidOrigin, normalize_origin

router = APIRouter(prefix="/web-logins", tags=["Web Logins"])


def get_repository(uow: UoWDep) -> WebLoginRepository:
    return WebLoginRepository(uow.session)


RepositoryDep = Annotated[WebLoginRepository, Depends(get_repository)]


class WebLoginResponse(BaseModel):
    id: UUID
    origin: str
    created_at: datetime
    updated_at: datetime
    last_used_at: datetime | None
    working: bool = Field(
        description=(
            "Whether the stored session still signs you in. False means it "
            "stopped working and the next run will ask you again."
        ),
    )


class WebLoginListResponse(BaseModel):
    items: list[WebLoginResponse]


class WebLoginAuditEntry(BaseModel):
    """One thing that was done with one saved login.

    `detail` is why, when the outcome was not plain "ok" — "session rejected",
    "nothing for this site was in the browser". It is the platform's own words
    rather than an agent's paraphrase, which is the point of reading it here.
    """

    origin: str
    action: str
    outcome: str
    detail: str | None
    conversation_id: UUID | None = Field(
        description=(
            "The run that did it, or null for something the person did "
            "themselves from the saved-logins screen."
        ),
    )
    created_at: datetime


class WebLoginAuditResponse(BaseModel):
    items: list[WebLoginAuditEntry]


def _view(login: WebLogin) -> WebLoginResponse:
    return WebLoginResponse(
        id=login.id,
        origin=login.origin,
        created_at=login.created_at,
        updated_at=login.updated_at,
        last_used_at=login.last_used_at,
        working=login.is_usable,
    )


@router.get(
    "",
    response_model=WebLoginListResponse,
    operation_id="web_login.list",
    summary="List saved site logins",
)
async def list_web_logins(
    user: CurrentUser, repository: RepositoryDep
) -> WebLoginListResponse:
    logins = await repository.list_for_user(user.id)
    return WebLoginListResponse(items=[_view(login) for login in logins])


@router.delete(
    "",
    response_model=WebLoginResponse,
    operation_id="web_login.delete",
    summary="Remove a saved site login",
)
async def delete_web_login(
    user: CurrentUser,
    repository: RepositoryDep,
    origin: str = Query(min_length=1, max_length=255),
) -> WebLoginResponse:
    """Forget a site.

    Removing the row is the whole revocation from Lemma's side. It does **not**
    sign the person out at the site, and the response says so — a saved session
    that has been deleted here is still a valid session there until they log out
    or it expires, and implying otherwise would be the more dangerous lie.
    """
    try:
        normalized = normalize_origin(origin)
    except InvalidOrigin as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        )
    try:
        removed = await repository.delete(user.id, normalized)
    except WebLoginNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Nothing saved for {normalized}",
        )
    await repository.record(
        user_id=user.id,
        origin=normalized,
        action="delete",
        outcome="ok",
        detail="removed by the person",
    )
    return _view(removed)


@router.get(
    "/history",
    response_model=WebLoginAuditResponse,
    operation_id="web_login.history",
    summary="What has been done with your saved logins",
)
async def web_login_history(
    user: CurrentUser,
    repository: RepositoryDep,
    limit: int = Query(default=100, ge=1, le=500),
) -> WebLoginAuditResponse:
    rows = await repository.history_for_user(user.id, limit=limit)
    return WebLoginAuditResponse(
        items=[
            WebLoginAuditEntry(
                origin=row.origin,
                action=row.action,
                outcome=row.outcome,
                detail=row.detail,
                conversation_id=row.conversation_id,
                created_at=row.created_at,
            )
            for row in rows
        ]
    )
