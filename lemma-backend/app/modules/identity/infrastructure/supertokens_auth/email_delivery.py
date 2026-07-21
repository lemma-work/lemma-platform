from __future__ import annotations

from typing import Any

from supertokens_python.ingredients.emaildelivery.types import EmailDeliveryInterface
from supertokens_python.recipe.emailpassword.types import (
    PasswordResetEmailTemplateVars,
)
from supertokens_python.recipe.emailverification.types import (
    VerificationEmailTemplateVars,
)

from app.core.email.email_sender import EmailSender
from app.core.email.transactional import EmailAction, render_transactional_email
from app.modules.identity.services.email_policy import (
    EmailPolicyError,
    validate_outbound_email,
)


class LemmaVerificationEmailService(
    EmailDeliveryInterface[VerificationEmailTemplateVars]
):
    async def send_email(
        self,
        template_vars: VerificationEmailTemplateVars,
        user_context: dict[str, Any],
    ) -> None:
        del user_context
        try:
            email = await validate_outbound_email(template_vars.user.email)
        except EmailPolicyError:
            return
        rendered = render_transactional_email(
            preheader="Verify your email to finish setting up Lemma.",
            eyebrow="Account security",
            heading="Verify your email",
            body=(
                "Confirm this email address to finish securing your Lemma account.",
                "This verification link is only for you.",
            ),
            action=EmailAction(
                label="Verify email",
                url=template_vars.email_verify_link,
            ),
            footer=(
                "If you did not create this account, you can safely ignore this message.",
            ),
        )
        await EmailSender.from_settings().send_email(
            email,
            "Verify your Lemma email",
            rendered.html,
            rendered.text,
            raise_on_failure=True,
        )


class LemmaPasswordResetEmailService(
    EmailDeliveryInterface[PasswordResetEmailTemplateVars]
):
    async def send_email(
        self,
        template_vars: PasswordResetEmailTemplateVars,
        user_context: dict[str, Any],
    ) -> None:
        del user_context
        try:
            email = await validate_outbound_email(template_vars.user.email)
        except EmailPolicyError:
            # Password-reset responses remain generic to avoid account-state
            # enumeration, while no email is sent to an inactive account.
            return
        rendered = render_transactional_email(
            preheader="Reset your Lemma password securely.",
            eyebrow="Account security",
            heading="Reset your password",
            body=(
                "Use the secure link below to choose a new Lemma password.",
                "If you did not request a password reset, your password has not changed.",
            ),
            action=EmailAction(
                label="Reset password",
                url=template_vars.password_reset_link,
            ),
            footer=(
                "If you did not request this, you can safely ignore this message.",
            ),
        )
        await EmailSender.from_settings().send_email(
            email,
            "Reset your Lemma password",
            rendered.html,
            rendered.text,
            raise_on_failure=True,
        )
