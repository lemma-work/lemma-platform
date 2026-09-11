"""Public share links for a pod's files: minting, listing and revoking them.

Its own controller rather than more of ``file_controller``, which is already
past the file-size ceiling: a share link has its own record, its own lifetime
and its own revocation, and the only thing it shares with the file endpoints is
the route prefix.

The link these mint is served — unauthenticated — by ``signed_file_controller``.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query, status

from app.core.api.dependencies import CurrentUser
from app.core.authorization.dependencies import PodContextDep, require_pod_membership
from app.modules.datastore.api.dependencies import FileServiceDep
from app.modules.datastore.api.file_response_mapping import (
    ensure_file_in_pod,
    to_file_response,
    to_public_file_path,
)
from app.modules.datastore.api.schemas.datastore_schemas import (
    FileSignedUrlRequest,
    FileSignedUrlResponse,
    SignedUrlListResponse,
    SignedUrlRevokeResponse,
    SignedUrlSummary,
)

router = APIRouter(
    prefix="/pods/{pod_id}/datastore/files",
    tags=["files"],
    redirect_slashes=False,
)


@router.post(
    "/signed-url",
    response_model=FileSignedUrlResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="file.signed_url",
    summary="Create a public, hit-capped signed URL for a file",
)
async def create_file_signed_url(
    pod_id: UUID,
    file_service: FileServiceDep,
    user: CurrentUser,
    ctx: PodContextDep,
    path: str = Query(...),
    body: FileSignedUrlRequest | None = None,
) -> FileSignedUrlResponse:
    body = body or FileSignedUrlRequest()
    (
        file_entity,
        signed_url,
        expires_at,
        max_hits,
    ) = await file_service.create_signed_url(
        pod_id,
        path,
        ctx=ctx,
        expires_seconds=body.expires_seconds,
        max_hits=body.max_hits,
    )
    public = to_file_response(file_entity, user.id)
    ensure_file_in_pod(public, pod_id)
    return FileSignedUrlResponse(
        path=public.path,
        signed_url=signed_url,
        expires_at=expires_at,
        max_hits=max_hits,
    )


@router.get(
    "/signed-urls",
    response_model=SignedUrlListResponse,
    operation_id="file.signed_url.list",
    summary="List this pod's public signed URLs",
    # Enumerating, and what it enumerates is capabilities: the `code` in each
    # row is the whole of what opening a link takes. Without this a non-member
    # could read a pod's codes and then fetch every file behind them.
    dependencies=[require_pod_membership("list share links", enumerates=True)],
)
async def list_file_signed_urls(
    pod_id: UUID,
    file_service: FileServiceDep,
    user: CurrentUser,
    ctx: PodContextDep,
    include_dead: bool = Query(
        False, description="Also list links that have expired or been revoked."
    ),
) -> SignedUrlListResponse:
    links = await file_service.list_signed_urls(
        pod_id, ctx=ctx, include_dead=include_dead
    )
    return SignedUrlListResponse(
        links=[
            SignedUrlSummary(
                code=link.code,
                path=to_public_file_path(
                    link.path,
                    current_user_id=user.id,
                    # The link records who minted it, and a personal path only
                    # reads as /me for the person whose path it is.
                    owner_user_id=link.created_by_user_id,
                ),
                filename=link.filename,
                content_type=link.content_type,
                size_bytes=link.size_bytes,
                max_hits=link.max_hits,
                expires_at=link.expires_at,
                revoked_at=link.revoked_at,
                exhausted_at=link.exhausted_at,
                created_at=link.created_at,
            )
            for link in links
        ]
    )


@router.delete(
    "/signed-urls/{code}",
    response_model=SignedUrlRevokeResponse,
    operation_id="file.signed_url.revoke",
    summary="Revoke a public signed URL",
    # The revoke itself is scoped by pod in the UPDATE, so a stranger could not
    # kill someone else's link — but without this they could still knock on the
    # door of any pod id they liked.
    dependencies=[require_pod_membership("revoke a share link")],
)
async def revoke_file_signed_url(
    pod_id: UUID,
    code: str,
    file_service: FileServiceDep,
    user: CurrentUser,
    ctx: PodContextDep,
) -> SignedUrlRevokeResponse:
    """Kill a link now rather than waiting out its expiry.

    Answers 200 either way: a code that is already dead, or was never this
    pod's, is reported as ``revoked: false`` rather than 404, so that a caller
    cleaning up cannot use this endpoint to discover which codes exist.
    """
    revoked = await file_service.revoke_signed_url(pod_id, code, ctx=ctx)
    return SignedUrlRevokeResponse(code=code, revoked=revoked)
