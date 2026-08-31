"""What is still standing between a surface and its first message.

Split from :mod:`setup_guides`, which describes how a platform is set up in
general — the same document for everyone, whatever state their surface is in.
This is the other question: given *this* surface, what has not been done yet.
One is reference, the other is a work list, and only the work list is allowed
to decide that a surface is not ready.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.modules.agent_surfaces.domain.entities import SurfacePlatform


class SurfaceSetupActionField(BaseModel):
    """A copy-able value the user pastes into their provider dashboard."""

    label: str
    value: str
    secret: bool = False


class SurfaceSetupAction(BaseModel):
    """A concrete thing the user must do to finish wiring up a surface.

    Only emitted when the user actually has to act (custom/bring-your-own-app
    credentials, or a pending OAuth grant). Each action carries where to go
    (``link``), ordered ``steps``, and the values to paste (``fields``).
    """

    key: str
    title: str
    description: str
    steps: list[str] = Field(default_factory=list)
    link: str | None = None
    link_label: str | None = None
    fields: list[SurfaceSetupActionField] = Field(default_factory=list)
    # Reference material, not a task: worth keeping to hand, but nothing is
    # waiting on it. A surface carrying only these is finished, and saying
    # otherwise puts "messages won't arrive until setup is finished" on a
    # surface that is already delivering them.
    informational: bool = False

    @property
    def is_blocking(self) -> bool:
        return not self.informational


def build_surface_setup_actions(
    *,
    platform: SurfacePlatform,
    is_custom_app: bool,
    webhook_url: str | None,
    slack_socket_mode: bool = False,
    slack_signing_secret_missing: bool = False,
    slack_app_id_missing: bool = False,
    slack_repair_url: str | None = None,
    whatsapp_verify_token: str | None = None,
) -> list[SurfaceSetupAction]:
    """The manual steps a user must complete for this surface — usually none.

    ``is_custom_app`` is true only when the connected account was set up with
    the org's *own* OAuth app (auth config ``ORG_CUSTOM``). When the account
    uses Lemma's own platform app (``SYSTEM_DEFAULT``), the webhook is already
    wired up centrally and the user has nothing to configure. Telegram
    (auto-registers its webhook) and email (Composio polling) never need manual
    webhook setup. Teams admin consent is handled separately because it applies
    to both system and custom apps.
    """
    if not is_custom_app:
        return []

    if platform is SurfacePlatform.SLACK:
        actions: list[SurfaceSetupAction] = []
        if slack_signing_secret_missing:
            actions.append(
                SurfaceSetupAction(
                    key="slack_signing_secret",
                    title="Add the Slack signing secret",
                    description=(
                        "This workspace uses its own Slack app, but its signing "
                        "secret is missing. Lemma rejects every event until the "
                        "secret from Slack's Basic Information page is saved."
                    ),
                    link=slack_repair_url,
                    link_label="Edit Slack credentials",
                    steps=[
                        "Open the Slack app's Basic Information page and copy its signing secret.",
                        "Edit this Slack connector in Lemma and save the signing secret.",
                    ],
                )
            )
        if slack_app_id_missing:
            actions.append(
                SurfaceSetupAction(
                    key="slack_app_id",
                    title="Reconnect this Slack workspace",
                    description=(
                        "This workspace uses its own Slack app, but Lemma never "
                        "recorded which app it is. Lemma rejects every event "
                        "until it can tell this app's events from another's, "
                        "and only Slack can tell it — reconnecting the account "
                        "records the app id as it signs in."
                    ),
                    link=slack_repair_url,
                    link_label="Reconnect Slack",
                    steps=[
                        "Open Connectors and find this Slack connection.",
                        "Reconnect it, so Slack returns the app id with the sign-in.",
                    ],
                )
            )
        if slack_socket_mode or not webhook_url:
            return actions
        # Reference, not instructions. An app made from Lemma's manifest already
        # has this URL and every event Lemma listens for — so telling someone to
        # set them by hand is at best noise, and at worst wrong: the list used to
        # name four events and the manifest declares six. Following it built an
        # app whose App Home never opened, because `app_home_opened` was missing.
        #
        # Kept for the app that was *not* made from the manifest, where this is
        # the only place the URL appears. Nothing here restates the events; that
        # list lives in the manifest, which is the thing that can't drift.
        actions.append(
            SurfaceSetupAction(
                key="slack_event_subscriptions",
                title="Where Slack sends messages",
                description=(
                    "This workspace runs its own Slack app. If you made it from "
                    "Lemma's manifest, it already points here and there's "
                    "nothing to do."
                ),
                link="https://api.slack.com/apps",
                link_label="Open your Slack apps",
                informational=True,
                fields=[
                    SurfaceSetupActionField(label="Request URL", value=webhook_url)
                ],
                steps=[
                    "Messages not arriving? Open your app on api.slack.com and "
                    "check ‘Event Subscriptions’ shows this URL as Verified.",
                    "If you changed anything, Slack may ask you to reinstall the "
                    "app to your workspace.",
                ],
            )
        )
        return actions

    if platform is SurfacePlatform.TEAMS:
        if not webhook_url:
            return []
        return [
            SurfaceSetupAction(
                key="teams_messaging_endpoint",
                title="Set your Teams bot's messaging endpoint",
                description=(
                    "Your tenant uses its own bot registration, so Teams needs "
                    "Lemma's messaging endpoint."
                ),
                link="https://portal.azure.com",
                link_label="Open Azure Portal",
                fields=[
                    SurfaceSetupActionField(
                        label="Messaging endpoint", value=webhook_url
                    )
                ],
                steps=[
                    "In the Azure Portal, open the Azure Bot resource for this tenant.",
                    "Open ‘Configuration’ and set ‘Messaging endpoint’ to the URL above.",
                    "Make sure the ‘Microsoft Teams’ channel is enabled on the bot.",
                    "Save your changes.",
                ],
            )
        ]

    if platform is SurfacePlatform.WHATSAPP:
        if not webhook_url:
            return []
        fields = [SurfaceSetupActionField(label="Callback URL", value=webhook_url)]
        if whatsapp_verify_token:
            fields.append(
                SurfaceSetupActionField(
                    label="Verify token", value=whatsapp_verify_token, secret=True
                )
            )
        return [
            SurfaceSetupAction(
                key="whatsapp_webhook",
                title="Configure your WhatsApp webhook",
                description="Your WhatsApp Business app needs to deliver messages to Lemma.",
                link="https://developers.facebook.com/apps",
                link_label="Open Meta for Developers",
                fields=fields,
                steps=[
                    "Open developers.facebook.com/apps and select your WhatsApp Business app.",
                    "Go to ‘WhatsApp → Configuration’.",
                    "Set the Callback URL and Verify token to the values above.",
                    "Subscribe to the ‘messages’ webhook field.",
                    "Click ‘Verify and save’.",
                ],
            )
        ]

    return []
