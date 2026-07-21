from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any

from supertokens_python.ingredients.emaildelivery.types import EmailDeliveryInterface
from supertokens_python.recipe.emailpassword.types import (
    PasswordResetEmailTemplateVars,
)
from supertokens_python.recipe.emailverification.types import (
    VerificationEmailTemplateVars,
)

from app.core.email.email_sender import EmailSender
from app.modules.identity.services.email_policy import (
    EmailPolicyError,
    validate_outbound_email,
)


_TEMPLATES = Path(__file__).resolve().parents[2] / "templates"


def _render(name: str, **values: Any) -> str:
    body = (_TEMPLATES / name).read_text(encoding="utf-8")
    return body.format(
        **{key: escape(str(value), quote=True) for key, value in values.items()}
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
        await EmailSender.from_settings().send_email(
            email,
            "Verify your Lemma email",
            _render("verify_email.html", verify_url=template_vars.email_verify_link),
            f"Verify your Lemma email: {template_vars.email_verify_link}",
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
        await EmailSender.from_settings().send_email(
            email,
            "Reset your Lemma password",
            _render(
                "password_reset_email.html", reset_url=template_vars.password_reset_link
            ),
            f"Reset your Lemma password: {template_vars.password_reset_link}",
            raise_on_failure=True,
        )
