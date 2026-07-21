from __future__ import annotations

from app.core.config import settings
from app.core.email.email_sender import EmailNotConfiguredError, EmailSender
from app.core.email.transactional import (
    EmailAction,
    EmailDetail,
    render_transactional_email,
)
from app.core.helpers.humanize import humanize_name
from app.core.log.log import get_logger
from app.modules.identity.domain.organization_entities import OrganizationRole
from app.modules.identity.domain.ports import IdentityEmailPort
from app.modules.identity.services.email_policy import (
    EmailPolicyError,
    validate_outbound_email,
)

logger = get_logger(__name__)


def _humanize_pod_name(value: str) -> str:
    """Humanize stored pod labels and avoid an onboarding-created ``Pod Pod``."""
    display_name = humanize_name(value)
    words = display_name.split()
    if len(words) >= 2 and words[-2].casefold() == words[-1].casefold() == "pod":
        words.pop()
    return " ".join(words)


class SmtpIdentityEmailAdapter(IdentityEmailPort):
    """SMTP adapter for identity notification emails."""

    def _display_name_from_email(self, email: str) -> str:
        local_part = email.split("@", 1)[0].split("+", 1)[0]
        name = " ".join(
            part for part in local_part.replace("_", ".").split(".") if part
        )
        return name.title() if name else email

    async def _send(
        self,
        *,
        to_email: str,
        subject: str,
        html_content: str,
        text_content: str,
    ) -> bool:
        try:
            to_email = await validate_outbound_email(to_email)
        except EmailPolicyError:
            return False
        try:
            sender = EmailSender.from_settings()
        except EmailNotConfiguredError:
            logger.debug(
                "identity.email_adapter.skipping_identity_email_because_smtp.diagnostic"
            )
            return False

        return await sender.send_email(
            to_email=to_email,
            subject=subject,
            html_content=html_content,
            text_content=text_content,
        )

    async def send_invitation_email(
        self,
        *,
        to_email: str,
        organization_name: str,
        inviter_email: str,
        role: OrganizationRole,
        accept_url: str,
        pod_name: str | None = None,
        pod_description: str | None = None,
    ) -> bool:
        del role
        display_organization_name = humanize_name(organization_name)
        display_pod_name = _humanize_pod_name(pod_name) if pod_name else pod_name
        inviter_name = self._display_name_from_email(inviter_email)

        if pod_name:
            target_label = f"pod {display_pod_name}"
            preheader = f"You have been invited to use {display_pod_name} on Lemma."
            eyebrow = "Pod invitation"
            heading = f"Use {display_pod_name}."
            body = (
                f"{inviter_name} invited you to access {display_pod_name} "
                f"in {display_organization_name}.",
            )
            action_label = f"Open {display_pod_name}"
            details = (
                EmailDetail("Pod", display_pod_name or ""),
                EmailDetail("Workspace", display_organization_name),
                EmailDetail(
                    "About",
                    pod_description
                    or f"{display_pod_name} is ready for you in {display_organization_name}.",
                ),
            )
            highlights: tuple[str, ...] = ()
        else:
            target_label = display_organization_name
            preheader = (
                f"You have been invited to join {display_organization_name} on Lemma."
            )
            eyebrow = "Workspace invitation"
            heading = f"Join {display_organization_name} on Lemma."
            body = (
                f"{inviter_name} invited you to the {display_organization_name} workspace.",
                "Accept the invitation to work with the team's agents, data, "
                "automations, and apps in one place.",
            )
            action_label = "Accept invitation"
            details = (EmailDetail("Workspace", display_organization_name),)
            highlights = (
                "Shared agents, data, and workspace apps",
                "Automations and tools for team workflows",
                "One place to build with your organization",
            )

        rendered = render_transactional_email(
            preheader=preheader,
            eyebrow=eyebrow,
            heading=heading,
            body=body,
            action=EmailAction(action_label, accept_url),
            details=details,
            highlights=highlights,
            footer=(
                f"This invitation was sent to {to_email}. Sign in with this email to accept.",
            ),
        )
        return await self._send(
            to_email=to_email,
            subject=f"Invitation to join {target_label}",
            html_content=rendered.html,
            text_content=rendered.text,
        )

    async def send_signup_welcome_email(
        self,
        *,
        to_email: str,
        first_name: str | None,
    ) -> bool:
        first_name_suffix = f", {first_name}" if first_name else ""
        rendered = render_transactional_email(
            preheader="Your Lemma account is ready.",
            eyebrow="Welcome to Lemma",
            heading=f"Welcome to Lemma{first_name_suffix}.",
            body=(
                "Your account is ready. Describe the work, connect the tools your team "
                "already uses, and start turning the process into a system.",
            ),
            action=EmailAction("Open Lemma", settings.frontend_url.rstrip("/")),
            highlights=(
                "Build agents, workflows, data, and apps together",
                "Connect the tools your team already uses",
                "Keep the work visible and repeatable",
            ),
            footer=(
                "You are receiving this because a Lemma account was verified with this email.",
            ),
        )
        return await self._send(
            to_email=to_email,
            subject="Welcome to Lemma",
            html_content=rendered.html,
            text_content=rendered.text,
        )

    async def send_invitation_accepted_email(
        self,
        *,
        to_email: str,
        organization_name: str,
        role: OrganizationRole,
    ) -> bool:
        del role
        display_organization_name = humanize_name(organization_name)
        rendered = render_transactional_email(
            preheader=f"You have joined {display_organization_name} on Lemma.",
            eyebrow="Workspace joined",
            heading=f"You're in {display_organization_name}.",
            body=(
                "The workspace is now available in your Lemma account. You can open "
                "shared pods, collaborate with the team, and connect the tools needed "
                "for your workflows.",
            ),
            action=EmailAction("Open Lemma", settings.frontend_url.rstrip("/")),
            details=(EmailDetail("Active workspace", display_organization_name),),
            footer=(
                "You are receiving this because an invitation was accepted for this email.",
            ),
        )
        return await self._send(
            to_email=to_email,
            subject=f"You joined {display_organization_name}",
            html_content=rendered.html,
            text_content=rendered.text,
        )

    async def send_pod_join_request_email(
        self,
        *,
        to_email: str,
        pod_name: str,
        organization_name: str,
        requester_name: str,
        requester_email: str,
    ) -> bool:
        display_pod_name = _humanize_pod_name(pod_name)
        display_organization_name = humanize_name(organization_name)
        requester_label = requester_name or requester_email
        rendered = render_transactional_email(
            preheader=f"{requester_label} asked to join {display_pod_name}.",
            eyebrow="Pod join request",
            heading=f"New request to join {display_pod_name}.",
            body=(
                f"{requester_label} requested access to {display_pod_name} in "
                f"{display_organization_name}.",
                "Review the pending request in Lemma to approve or decline it.",
            ),
            action=EmailAction("Review requests", settings.frontend_url.rstrip("/")),
            details=(
                EmailDetail("Requester", requester_label),
                EmailDetail("Email", requester_email),
                EmailDetail("Pod", display_pod_name),
                EmailDetail("Workspace", display_organization_name),
            ),
            footer=(
                f"This notification was sent to {to_email} because you are a pod admin.",
            ),
        )
        return await self._send(
            to_email=to_email,
            subject=f"Request to join {display_pod_name}",
            html_content=rendered.html,
            text_content=rendered.text,
        )
