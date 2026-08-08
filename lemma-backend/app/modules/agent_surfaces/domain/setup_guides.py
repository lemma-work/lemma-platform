from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from app.modules.agent_surfaces.domain.entities import SurfacePlatform


class SurfaceSetupMode(str, Enum):
    CONNECTED_ACCOUNT = "CONNECTED_ACCOUNT"
    PLATFORM_BUILT_IN = "PLATFORM_BUILT_IN"


class SurfaceSetupFieldSource(str, Enum):
    CREATE_REQUEST = "CREATE_REQUEST"
    CREATE_RESPONSE = "CREATE_RESPONSE"


class SurfaceSetupPhase(str, Enum):
    PREPARE = "PREPARE"
    CREATE_SURFACE = "CREATE_SURFACE"
    CONFIGURE_PROVIDER = "CONFIGURE_PROVIDER"
    VERIFY = "VERIFY"


class SurfaceSetupField(BaseModel):
    name: str
    label: str
    source: SurfaceSetupFieldSource
    description: str
    required: bool = True
    secret: bool = False
    example: str | None = None


class SurfaceSetupStep(BaseModel):
    phase: SurfaceSetupPhase
    title: str
    description: str


class SurfaceConnectorSetupGuide(BaseModel):
    mode: SurfaceSetupMode
    title: str
    summary: str
    supported: bool = True
    docs_path: str | None = None
    fields: list[SurfaceSetupField] = Field(default_factory=list)
    steps: list[SurfaceSetupStep] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class SurfacePlatformSetupGuide(BaseModel):
    platform: SurfacePlatform
    title: str
    summary: str
    docs_path: str
    connectors: list[SurfaceConnectorSetupGuide] = Field(default_factory=list)


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
        if slack_socket_mode or not webhook_url:
            return []
        # Reference, not instructions. An app made from Lemma's manifest already
        # has this URL and every event Lemma listens for — so telling someone to
        # set them by hand is at best noise, and at worst wrong: the list used to
        # name four events and the manifest declares six. Following it built an
        # app whose App Home never opened, because `app_home_opened` was missing.
        #
        # Kept for the app that was *not* made from the manifest, where this is
        # the only place the URL appears. Nothing here restates the events; that
        # list lives in the manifest, which is the thing that can't drift.
        return [
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
                fields=[SurfaceSetupActionField(label="Request URL", value=webhook_url)],
                steps=[
                    "Messages not arriving? Open your app on api.slack.com and "
                    "check ‘Event Subscriptions’ shows this URL as Verified.",
                    "If you changed anything, Slack may ask you to reinstall the "
                    "app to your workspace.",
                ],
            )
        ]

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
                    SurfaceSetupActionField(label="Messaging endpoint", value=webhook_url)
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


def build_surface_setup_guide(platform: SurfacePlatform) -> SurfacePlatformSetupGuide:
    if platform is SurfacePlatform.SLACK:
        return _connected_account_guide(
            platform=platform,
            title="Slack Surface Setup",
            docs_path="docs/surfaces/slack.md",
            account_label="Connected Slack account",
            account_description="Existing Lemma connector account for the Slack workspace.",
            channel_example="C1234567890",
            notes=[
                "Slack requires account_id. Lemma provides the default Slack auth config, but the user still connects their Slack workspace account.",
                "DM surfaces are unique per workspace/account. Channel surfaces can reuse the same account_id with different external_channel_id values.",
                "CHANNEL mode replies only for mentions or thread continuation.",
                "Standalone/local workers use Socket Mode when ENABLE_SLACK_SOCKET_MODE=true and SLACK_APP_TOKEN is configured.",
            ],
        )
    if platform is SurfacePlatform.TEAMS:
        return _connected_account_guide(
            platform=platform,
            title="Microsoft Teams Surface Setup",
            docs_path="docs/surfaces/teams.md",
            account_label="Connected Teams account",
            account_description="Existing Lemma connector account for the Microsoft tenant.",
            channel_example="19:channel@thread.tacv2",
            notes=[
                "Teams requires account_id. Lemma derives external_tenant_id from the connected account.",
                "DM surfaces are personal chat bindings. Channel surfaces require external_channel_id and respond to mentions or thread continuation.",
            ],
        )
    if platform is SurfacePlatform.WHATSAPP:
        return _built_in_or_account_guide(
            platform=platform,
            title="WhatsApp Surface Setup",
            docs_path="docs/surfaces/whatsapp.md",
            built_in_title="Lemma-managed WhatsApp number",
            account_label="Connected WhatsApp account",
            account_description="Optional Lemma connector account for a customer-managed WhatsApp phone number.",
            notes=[
                "account_id may be null when using Lemma's built-in WhatsApp credentials.",
                "If a customer-managed WhatsApp setup is needed, model it as a connector account and pass that account_id.",
            ],
        )
    if platform is SurfacePlatform.TELEGRAM:
        return _built_in_or_account_guide(
            platform=platform,
            title="Telegram Surface Setup",
            docs_path="docs/surfaces/telegram.md",
            built_in_title="Lemma-managed Telegram bot",
            account_label="Connected Telegram account",
            account_description="Optional Lemma connector account for a customer-managed Telegram bot.",
            notes=[
                "account_id may be null when using Lemma's built-in Telegram bot.",
                "If a customer-managed Telegram bot is needed, model the bot token as a connector account and pass that account_id.",
                "Standalone/local workers clear the Telegram webhook and poll automatically for the built-in bot.",
            ],
        )
    if platform is SurfacePlatform.GMAIL:
        return _email_account_guide(
            platform=platform,
            title="Gmail Surface Setup",
            docs_path="docs/surfaces/gmail.md",
            account_label="Connected Gmail account",
            account_description="Existing Lemma connector account for the Gmail mailbox.",
        )
    if platform is SurfacePlatform.OUTLOOK:
        return _email_account_guide(
            platform=platform,
            title="Outlook Surface Setup",
            docs_path="docs/surfaces/outlook.md",
            account_label="Connected Outlook account",
            account_description="Existing Lemma connector account for the Outlook mailbox.",
        )
    raise ValueError(f"Unsupported surface platform: {platform}")


def _common_fields(
    *,
    include_channel: bool,
    account_label: str | None = None,
    account_description: str | None = None,
    channel_example: str | None = None,
) -> list[SurfaceSetupField]:
    fields: list[SurfaceSetupField] = []
    if account_label and account_description:
        fields.append(
            SurfaceSetupField(
                name="account_id",
                label=account_label,
                source=SurfaceSetupFieldSource.CREATE_REQUEST,
                description=account_description,
            )
        )
    fields.extend(
        [
            SurfaceSetupField(
                name="mode",
                label="Surface mode",
                source=SurfaceSetupFieldSource.CREATE_REQUEST,
                description="DM, CHANNEL, or EMAIL depending on the platform.",
            ),
            SurfaceSetupField(
                name="dm_conversation_reset_after_hours",
                label="DM reset window",
                source=SurfaceSetupFieldSource.CREATE_REQUEST,
                description="Hours of inactivity after which DM mode starts a new Lemma conversation.",
                required=False,
                example="24",
            ),
        ]
    )
    if include_channel:
        fields.append(
            SurfaceSetupField(
                name="external_channel_id",
                label="External channel ID",
                source=SurfaceSetupFieldSource.CREATE_REQUEST,
                description="Required when mode=CHANNEL.",
                required=False,
                example=channel_example,
            )
        )
    return fields


def _connected_account_guide(
    *,
    platform: SurfacePlatform,
    title: str,
    docs_path: str,
    account_label: str,
    account_description: str,
    channel_example: str,
    notes: list[str],
) -> SurfacePlatformSetupGuide:
    return SurfacePlatformSetupGuide(
        platform=platform,
        title=title,
        summary="Create surfaces from connected connector accounts; credentials are not stored on the surface.",
        docs_path=docs_path,
        connectors=[
            SurfaceConnectorSetupGuide(
                mode=SurfaceSetupMode.CONNECTED_ACCOUNT,
                title="Connected account",
                summary="Use an existing connector account and choose DM or CHANNEL mode.",
                docs_path=docs_path,
                fields=_common_fields(
                    include_channel=True,
                    account_label=account_label,
                    account_description=account_description,
                    channel_example=channel_example,
                ),
                steps=[
                    SurfaceSetupStep(
                        phase=SurfaceSetupPhase.PREPARE,
                        title="Connect the account",
                        description="Complete the connector connection flow and keep the returned account_id.",
                    ),
                    SurfaceSetupStep(
                        phase=SurfaceSetupPhase.CREATE_SURFACE,
                        title="Create the surface",
                        description="POST the surface with platform, mode, account_id, and external_channel_id when using CHANNEL mode.",
                    ),
                    SurfaceSetupStep(
                        phase=SurfaceSetupPhase.VERIFY,
                        title="Verify routing",
                        description="Send a DM or mention the agent in the configured channel.",
                    ),
                ],
                notes=notes,
            )
        ],
    )


def _built_in_or_account_guide(
    *,
    platform: SurfacePlatform,
    title: str,
    docs_path: str,
    built_in_title: str,
    account_label: str,
    account_description: str,
    notes: list[str],
) -> SurfacePlatformSetupGuide:
    return SurfacePlatformSetupGuide(
        platform=platform,
        title=title,
        summary="Use Lemma's built-in credentials by default, or bind a connector account when the platform setup is customer-managed.",
        docs_path=docs_path,
        connectors=[
            SurfaceConnectorSetupGuide(
                mode=SurfaceSetupMode.PLATFORM_BUILT_IN,
                title=built_in_title,
                summary="Create a DM surface without account_id and route through Lemma's platform webhook.",
                docs_path=docs_path,
                fields=_common_fields(include_channel=False),
                steps=[
                    SurfaceSetupStep(
                        phase=SurfaceSetupPhase.CREATE_SURFACE,
                        title="Create the surface",
                        description="POST the surface with platform and mode=DM. Leave account_id null.",
                    ),
                    SurfaceSetupStep(
                        phase=SurfaceSetupPhase.VERIFY,
                        title="Test the chat",
                        description="Send a message to the Lemma-managed bot or number and confirm the agent responds.",
                    ),
                ],
                notes=notes,
            ),
            SurfaceConnectorSetupGuide(
                mode=SurfaceSetupMode.CONNECTED_ACCOUNT,
                title="Connected customer-managed account",
                summary="Use a connector account when this workspace/customer owns the bot or phone number.",
                docs_path=docs_path,
                fields=_common_fields(
                    include_channel=False,
                    account_label=account_label,
                    account_description=account_description,
                ),
                steps=[
                    SurfaceSetupStep(
                        phase=SurfaceSetupPhase.PREPARE,
                        title="Connect the account",
                        description="Create or connect the platform connector account and keep the account_id.",
                    ),
                    SurfaceSetupStep(
                        phase=SurfaceSetupPhase.CREATE_SURFACE,
                        title="Create the surface",
                        description="POST the surface with platform, mode=DM, and account_id.",
                    ),
                ],
                notes=notes,
            ),
        ],
    )


def _email_account_guide(
    *,
    platform: SurfacePlatform,
    title: str,
    docs_path: str,
    account_label: str,
    account_description: str,
) -> SurfacePlatformSetupGuide:
    return SurfacePlatformSetupGuide(
        platform=platform,
        title=title,
        summary="Email surfaces use EMAIL mode and a connected mailbox account.",
        docs_path=docs_path,
        connectors=[
            SurfaceConnectorSetupGuide(
                mode=SurfaceSetupMode.CONNECTED_ACCOUNT,
                title="Connected mailbox",
                summary="Use an existing connector account and let Lemma create the polling trigger.",
                docs_path=docs_path,
                fields=_common_fields(
                    include_channel=False,
                    account_label=account_label,
                    account_description=account_description,
                ),
                steps=[
                    SurfaceSetupStep(
                        phase=SurfaceSetupPhase.PREPARE,
                        title="Connect the mailbox",
                        description="Complete the email connector connection flow and keep the account_id.",
                    ),
                    SurfaceSetupStep(
                        phase=SurfaceSetupPhase.CREATE_SURFACE,
                        title="Create the email surface",
                        description="POST the surface with platform, mode=EMAIL, and account_id.",
                    ),
                    SurfaceSetupStep(
                        phase=SurfaceSetupPhase.VERIFY,
                        title="Verify reply flow",
                        description="Send an inbound email and confirm Lemma creates or reuses the mapped conversation thread.",
                    ),
                ],
            )
        ],
    )
