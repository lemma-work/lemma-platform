from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, ValidationError
from sqlalchemy import func, select
from supertokens_python.recipe.session.asyncio import revoke_all_sessions_for_user

from app.core.config import reveal_secret, settings
from app.core.infrastructure.db.session import async_session_maker
from app.modules.identity.domain.email import normalize_identity_email
from app.modules.identity.infrastructure.models.user_models import User
from app.modules.identity.infrastructure.user_cache import get_user_cache

router = APIRouter(
    prefix="/auth/email/bounces",
    tags=["Auth"],
    redirect_slashes=False,
)


class BounceEvent(BaseModel):
    email: EmailStr
    event: Literal["hard_bounce", "soft_bounce"]


class ResendBounceDetails(BaseModel):
    type: Literal["Permanent", "Temporary"]


class ResendBounceData(BaseModel):
    to: list[EmailStr]
    bounce: ResendBounceDetails


class ResendBounceEvent(BaseModel):
    type: Literal["email.bounced"]
    data: ResendBounceData


def verify_bounce_signature(
    *,
    timestamp: str,
    signature: str,
    body: bytes,
    secret: str,
    now: int | None = None,
) -> None:
    """Validate the provider adapter's timestamped HMAC envelope."""
    try:
        timestamp_value = int(timestamp)
    except ValueError as exc:
        raise HTTPException(
            status_code=401, detail="Invalid webhook signature"
        ) from exc
    if abs((now if now is not None else int(time.time())) - timestamp_value) > 300:
        raise HTTPException(status_code=401, detail="Webhook timestamp expired")
    expected = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256
    ).hexdigest()
    candidate = signature.removeprefix("sha256=")
    if not hmac.compare_digest(expected, candidate):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")


def verify_resend_webhook_signature(
    *,
    message_id: str,
    timestamp: str,
    signature: str,
    body: bytes,
    secret: str,
    now: int | None = None,
) -> None:
    """Validate a Resend/Svix webhook against the unmodified request body."""
    try:
        timestamp_value = int(timestamp)
    except ValueError as exc:
        raise HTTPException(
            status_code=401, detail="Invalid webhook signature"
        ) from exc
    if abs((now if now is not None else int(time.time())) - timestamp_value) > 300:
        raise HTTPException(status_code=401, detail="Webhook timestamp expired")

    encoded_secret = secret.removeprefix("whsec_")
    try:
        signing_key = base64.b64decode(encoded_secret, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(
            status_code=401, detail="Invalid webhook signature"
        ) from exc
    signed_payload = f"{message_id}.{timestamp}.".encode() + body
    expected = base64.b64encode(
        hmac.new(signing_key, signed_payload, hashlib.sha256).digest()
    ).decode()
    candidates = (
        item.removeprefix("v1,") for item in signature.split() if item.startswith("v1,")
    )
    if not any(hmac.compare_digest(expected, candidate) for candidate in candidates):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")


async def _deactivate_email_for_hard_bounce(email_address: str) -> None:
    email = normalize_identity_email(email_address)
    async with async_session_maker() as db_session:
        user = await db_session.scalar(
            select(User).where(func.lower(User.email) == email)
        )
        if user is not None and user.is_active and not user.is_deleted:
            user.is_active = False
            user.deactivated_at = user.deactivated_at or datetime.now(timezone.utc)
            user.deactivation_reason = "HARD_BOUNCE"
            await db_session.commit()
            await get_user_cache().invalidate(user.id)
            await revoke_all_sessions_for_user(str(user.id))


@router.post("", include_in_schema=False, status_code=204)
async def accept_email_bounce(request: Request, event: BounceEvent) -> Response:
    secret = reveal_secret(settings.auth_bounce_webhook_secret)
    if not secret:
        raise HTTPException(status_code=404, detail="Not found")
    body = await request.body()
    verify_bounce_signature(
        timestamp=request.headers.get("x-lemma-timestamp", ""),
        signature=request.headers.get("x-lemma-signature", ""),
        body=body,
        secret=secret,
    )
    if event.event == "hard_bounce":
        await _deactivate_email_for_hard_bounce(str(event.email))
    return Response(status_code=204)


@router.post("/resend", include_in_schema=False, status_code=204)
async def accept_resend_email_bounce(request: Request) -> Response:
    """Consume signed Resend bounce events without relying on a paid service."""
    secret = reveal_secret(settings.resend_webhook_secret)
    if not secret:
        raise HTTPException(status_code=404, detail="Not found")
    body = await request.body()
    verify_resend_webhook_signature(
        message_id=request.headers.get("svix-id", ""),
        timestamp=request.headers.get("svix-timestamp", ""),
        signature=request.headers.get("svix-signature", ""),
        body=body,
        secret=secret,
    )
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid webhook payload") from exc
    if not isinstance(payload, dict) or payload.get("type") != "email.bounced":
        return Response(status_code=204)

    try:
        event = ResendBounceEvent.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail="Invalid webhook payload") from exc
    if event.data.bounce.type == "Temporary":
        return Response(status_code=204)
    for recipient in event.data.to:
        await _deactivate_email_for_hard_bounce(str(recipient))
    return Response(status_code=204)
