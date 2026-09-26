"""Whether this installation can send email, and a way to prove it.

For a local installation -- Lemma Desktop -- where the person who set email up
is the person signed in, and the only way they would otherwise find out a
setting is wrong is an invitation that never arrives. Hosted and self-hosted
servers answer 404: their operators configure mail outside the product, and a
button that sends mail on request is not something to leave on the internet.

It stays available while a Desktop installation is shared, because that is when
the owner sets email up -- to invite people. The test is sent only to the
caller's own account address, which anybody able to sign up could already mail
through the welcome email, and it is rate limited per account whenever the auth
abuse controls are on, which sharing turns on.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.core.api.dependencies import UoWDep
from app.core.authorization.dependencies import reject_delegated_workload_anywhere
from app.core.config import settings
from app.core.email.email_sender import (
    EmailDeliveryError,
    EmailNotConfiguredError,
    EmailSender,
    email_delivery_configured,
)
from app.core.email.transactional import render_transactional_email
from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.log.log import get_logger
from app.modules.identity.infrastructure.user_repositories import UserRepository
from app.modules.identity.services.auth_abuse import (
    RateLimitExceeded,
    get_auth_abuse_store,
)

logger = get_logger(__name__)

router = APIRouter(
    prefix="/users/me/email-delivery",
    tags=["Users"],
    redirect_slashes=False,
)

#: A handful of tries while fixing a setting, not a way to send mail.
_TEST_LIMIT = 5
_TEST_WINDOW_SECONDS = 600

_NOT_CONFIGURED_MESSAGE = (
    "Email isn't set up on this server. Add a Resend API key and sender, or "
    "SMTP settings, then try again."
)
_DELIVERY_FAILED_MESSAGE = (
    "The mail server refused or could not be reached. Check the host, port, "
    "username, password and sender address, and that the sender's domain is "
    "verified with your provider."
)


class EmailDeliveryStatusResponse(BaseModel):
    configured: bool = Field(
        description=(
            "Whether mail sent from this server can reach an inbox. False while "
            "no provider is set up, and while mail is written to a local spool "
            "(EMAIL_TRANSPORT=filesystem) instead of being sent."
        )
    )


class EmailDeliveryTestResponse(BaseModel):
    ok: bool = Field(description="Whether the test email was handed to the provider")
    message: str = Field(
        description=(
            "What happened, in words to show the person: where the email went, "
            "or what to change. Never the provider's own error."
        )
    )


def _require_local_installation() -> None:
    if not settings.is_local_mode():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")


@router.get(
    "",
    status_code=status.HTTP_200_OK,
    operation_id="user.email_delivery.get",
    summary="Get Email Delivery Status",
    description=(
        "Whether this local installation can send email. Local installations "
        "only; 404 elsewhere."
    ),
    response_model=EmailDeliveryStatusResponse,
)
async def get_email_delivery() -> EmailDeliveryStatusResponse:
    _require_local_installation()
    return EmailDeliveryStatusResponse(configured=email_delivery_configured())


@router.post(
    "/test",
    status_code=status.HTTP_200_OK,
    operation_id="user.email_delivery.test",
    dependencies=[reject_delegated_workload_anywhere("send a test email")],
    summary="Send A Test Email",
    description=(
        "Send a short test email to the signed-in user's own address with this "
        "installation's email settings. Local installations only; 404 elsewhere."
    ),
    response_model=EmailDeliveryTestResponse,
    responses={429: {"description": "Too many test emails; see Retry-After"}},
)
async def send_test_email(request: Request, uow: UoWDep) -> EmailDeliveryTestResponse:
    _require_local_installation()
    principal = request.state.user
    user = await UserRepository(uow).get(principal.id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    # Everything after the one read is Redis and a mail server, which can take
    # the SMTP timeout to answer; neither needs the pooled connection.
    async with connection_released(getattr(uow, "session", None)):
        try:
            await get_auth_abuse_store().enforce(
                f"identity:rate:email-test:user:{principal.id}",
                limit=_TEST_LIMIT,
                window_seconds=_TEST_WINDOW_SECONDS,
            )
        except RateLimitExceeded as exc:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many test emails. Wait a few minutes and try again.",
                headers={"Retry-After": str(exc.retry_after_seconds)},
            ) from exc
        return await _send_test_email(str(user.email))


async def _send_test_email(to_email: str) -> EmailDeliveryTestResponse:
    # The spool "delivers" every time, so passing through it would report a
    # success nobody receives.
    if settings.email_transport == "filesystem":
        return EmailDeliveryTestResponse(ok=False, message=_NOT_CONFIGURED_MESSAGE)
    try:
        sender = EmailSender.from_settings()
    except EmailNotConfiguredError as not_configured:
        # The configuration's own sentence names the missing variable and holds
        # no secret, and the person reading it is the one who set it.
        return EmailDeliveryTestResponse(ok=False, message=str(not_configured))

    rendered = render_transactional_email(
        preheader="Email from your Lemma works.",
        eyebrow="Test email",
        heading="Email is working",
        body=(
            (
                "This is the test email you asked Lemma to send. Invitations "
                "and password resets from this Lemma will arrive the same way."
            ),
        ),
        footer=(f"Sent to {to_email} because you asked for a test email.",),
    )
    try:
        await sender.send_email(
            to_email=to_email,
            subject="Lemma test email",
            html_content=rendered.html,
            text_content=rendered.text,
            raise_on_failure=True,
        )
    except EmailDeliveryError:
        # `send_email` has already logged the provider's error with its
        # traceback; the response carries only what to check.
        logger.warning("identity.email_delivery.test_failed")
        return EmailDeliveryTestResponse(ok=False, message=_DELIVERY_FAILED_MESSAGE)
    logger.info("identity.email_delivery.test_sent")
    return EmailDeliveryTestResponse(
        ok=True,
        message=f"Sent a test email to {to_email}. It can take a minute to arrive.",
    )
