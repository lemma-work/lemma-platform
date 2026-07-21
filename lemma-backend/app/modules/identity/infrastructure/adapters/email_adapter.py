from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any

from app.modules.identity.domain.organization_entities import OrganizationRole
from app.modules.identity.domain.ports import IdentityEmailPort
from app.core.email.email_sender import EmailNotConfiguredError, EmailSender
from app.core.helpers.humanize import humanize_name
from app.core.log.log import get_logger
from app.modules.identity.services.email_policy import (
    EmailPolicyError,
    validate_outbound_email,
)

logger = get_logger(__name__)


class SmtpIdentityEmailAdapter(IdentityEmailPort):
    """SMTP adapter for identity notification emails."""

    def __init__(self):
        self._templates_dir = Path(__file__).resolve().parents[2] / "templates"

    def _render(self, template_name: str, **kwargs: Any) -> str:
        template_path = self._templates_dir / template_name
        body = template_path.read_text(encoding="utf-8")
        return body.format(**kwargs)

    def _safe(self, value: object | None) -> str:
        return escape(str(value or ""), quote=True)

    def _display_name_from_email(self, email: str) -> str:
        local_part = email.split("@", 1)[0].split("+", 1)[0]
        name = " ".join(
            part for part in local_part.replace("_", ".").split(".") if part
        )
        return name.title() if name else email

    def _feature_row(self, icon: str, label: str) -> str:
        return (
            "<tr>"
            '<td width="42" style="padding:14px 8px 14px 18px;vertical-align:middle;">'
            '<span style="display:inline-block;width:30px;height:30px;border-radius:999px;'
            'background:#f1efe9;color:#24211d;text-align:center;line-height:30px;font-size:14px;">'
            f"{icon}</span>"
            "</td>"
            '<td style="padding:14px 18px 14px 8px;border-bottom:1px solid #e8e4dc;'
            'color:#5f5a52;font-size:15px;line-height:1.45;font-weight:600;">'
            f"{label}</td>"
            "</tr>"
        )

    def _description_card(self, description: str) -> str:
        return (
            '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
            'style="border:1px solid #dedbd2;border-radius:14px;background:#fbfaf6;">'
            '<tr><td style="padding:20px 22px;color:#5f5a52;font-size:15px;line-height:1.65;">'
            f"{description}</td></tr></table>"
        )

    def _feature_card(self, rows: list[tuple[str, str]]) -> str:
        row_html = "".join(
            self._feature_row(icon, self._safe(label)) for icon, label in rows
        )
        row_html = row_html.rsplit("border-bottom:1px solid #e8e4dc;", 1)
        if len(row_html) == 2:
            row_html = "border-bottom:0;".join(row_html)
        else:
            row_html = row_html[0]
        return (
            '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
            'style="border:1px solid #dedbd2;border-radius:14px;background:#fbfaf6;">'
            f"{row_html}</table>"
        )

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
        display_organization_name = humanize_name(organization_name)
        display_pod_name = humanize_name(pod_name) if pod_name else pod_name
        safe_organization_name = self._safe(display_organization_name)
        safe_pod_name = self._safe(display_pod_name)
        safe_inviter_name = self._safe(self._display_name_from_email(inviter_email))
        safe_to_email = self._safe(to_email)
        safe_accept_url = self._safe(accept_url)

        if pod_name:
            target_label = f"pod {display_pod_name}"
            preheader = f"You have been invited to use {display_pod_name} on Lemma."
            invitation_label = "Pod invitation"
            heading = f"Use {safe_pod_name}."
            subheading = (
                f'<strong style="color:#24211d;">{safe_inviter_name}</strong> '
                f"invited you to access {safe_pod_name} in {safe_organization_name}."
            )
            button_label = f"Open {safe_pod_name}"
            details_html = self._description_card(
                self._safe(pod_description)
                if pod_description
                else f"{safe_pod_name} is ready for you in {safe_organization_name}."
            )
            invite_copy = (
                f"{self._display_name_from_email(inviter_email)} invited you to "
                f"access {display_pod_name} in {display_organization_name}."
            )
        else:
            target_label = display_organization_name
            preheader = (
                f"You have been invited to join {display_organization_name} on Lemma."
            )
            invitation_label = "Workspace invitation"
            heading = f"Join {safe_organization_name} on Lemma."
            subheading = (
                f'<strong style="color:#24211d;">{safe_inviter_name}</strong> '
                f"invited you to the {safe_organization_name} workspace. Accept the invite "
                "to start working with the team's agents, data, automations, and apps in one place."
            )
            button_label = "Accept invitation"
            details_html = self._feature_card(
                [
                    ("&#9675;", "Shared agents, data, and workspace apps"),
                    ("&#9633;", "Automations and tools for team workflows"),
                    ("&#10003;", "One place to build with your organization"),
                ]
            )
            invite_copy = (
                f"{self._display_name_from_email(inviter_email)} invited you to "
                f"join {display_organization_name} on Lemma."
            )

        html_content = self._render(
            "invitation_email.html",
            preheader=self._safe(preheader),
            invitation_label=self._safe(invitation_label),
            heading=heading,
            subheading=subheading,
            button_label=button_label,
            details_html=details_html,
            to_email=safe_to_email,
            accept_url=safe_accept_url,
        )
        pod_suffix = (
            f" Pod: {display_pod_name}."
            + (f" Description: {pod_description}." if pod_description else "")
            if pod_name
            else ""
        )
        text_content = f"{invite_copy}{pod_suffix} Accept the invitation: {accept_url}"
        return await self._send(
            to_email=to_email,
            subject=f"Invitation to join {target_label}",
            html_content=html_content,
            text_content=text_content,
        )

    async def send_signup_welcome_email(
        self,
        *,
        to_email: str,
        first_name: str | None,
    ) -> bool:
        first_name_suffix = f", {first_name}" if first_name else ""
        html_content = self._render(
            "welcome_email.html",
            first_name_suffix=self._safe(first_name_suffix),
        )
        text_content = f"Welcome to Lemma{first_name_suffix}. Your account is ready."
        return await self._send(
            to_email=to_email,
            subject="Welcome to Lemma",
            html_content=html_content,
            text_content=text_content,
        )

    async def send_invitation_accepted_email(
        self,
        *,
        to_email: str,
        organization_name: str,
        role: OrganizationRole,
    ) -> bool:
        display_organization_name = humanize_name(organization_name)
        html_content = self._render(
            "invitation_accepted_email.html",
            organization_name=self._safe(display_organization_name),
        )
        text_content = (
            f"You joined {display_organization_name}. "
            "You can now access this workspace in Lemma."
        )
        return await self._send(
            to_email=to_email,
            subject=f"You joined {display_organization_name}",
            html_content=html_content,
            text_content=text_content,
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
        display_pod_name = humanize_name(pod_name)
        display_organization_name = humanize_name(organization_name)
        requester_label = requester_name or requester_email
        safe_pod_name = self._safe(display_pod_name)
        safe_organization_name = self._safe(display_organization_name)
        safe_requester = self._safe(requester_label)
        safe_requester_email = self._safe(requester_email)

        preheader = f"{requester_label} asked to join {display_pod_name}."
        heading = f"New request to join {safe_pod_name}."
        subheading = (
            f'<strong style="color:#24211d;">{safe_requester}</strong> '
            f"requested to join {safe_pod_name} in {safe_organization_name}."
        )
        details_html = self._description_card(
            f"{safe_requester} (&lt;{safe_requester_email}&gt;) is waiting for approval "
            f"to access {safe_pod_name}."
        )
        html_content = self._render(
            "pod_join_request_email.html",
            preheader=self._safe(preheader),
            label=self._safe("Pod join request"),
            heading=heading,
            subheading=subheading,
            details_html=details_html,
            to_email=self._safe(to_email),
        )
        text_content = (
            f"{requester_label} ({requester_email}) requested to join "
            f"{display_pod_name} in {display_organization_name}. "
            "Review pending join requests in Lemma to approve or decline."
        )
        return await self._send(
            to_email=to_email,
            subject=f"Request to join {display_pod_name}",
            html_content=html_content,
            text_content=text_content,
        )
