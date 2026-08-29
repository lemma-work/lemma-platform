from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.domain.errors import AgentSurfaceValidationError


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
    if platform is SurfacePlatform.RESEND:
        return _system_email_guide()
    # Every ``SurfacePlatform`` member is answered above, and
    # ``test_every_surface_platform_has_a_setup_guide`` keeps it that way. This
    # remains for a member added without a guide: a typed error the API maps to
    # a clean response, not the bare ``ValueError`` that used to reach callers
    # as a 500 for RESEND -- a platform that was always valid and always
    # auto-provisioned, so the 500 was reachable without anyone configuring
    # anything.
    raise AgentSurfaceValidationError(f"Unsupported surface platform: {platform}")


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


def _system_email_guide() -> SurfacePlatformSetupGuide:
    """Resend: the one surface nobody connects.

    Every agent is given a Resend mailbox at creation, on Lemma's own
    credentials, so there is no connector account and no account_id. The guide
    exists to say that -- and to say what the operator must configure once, at
    deployment level, for the surface to work at all.
    """
    return SurfacePlatformSetupGuide(
        platform=SurfacePlatform.RESEND,
        title="Resend Surface Setup",
        summary="Provisioned automatically with every agent on Lemma-managed credentials; nothing to connect.",
        docs_path="docs/surfaces/resend.md",
        connectors=[
            SurfaceConnectorSetupGuide(
                mode=SurfaceSetupMode.PLATFORM_BUILT_IN,
                title="Lemma-managed Resend mailbox",
                summary="Created with the agent. The address is derived, not chosen, and routing matches inbound mail on it.",
                docs_path="docs/surfaces/resend.md",
                fields=[
                    SurfaceSetupField(
                        name="surface_identity_email",
                        label="Provisioned address",
                        source=SurfaceSetupFieldSource.CREATE_RESPONSE,
                        description=(
                            "The inbound address, and the From on replies. Derived per agent; the "
                            "pod-level fallback is pod-<pod id hex>@<RESEND_INBOUND_DOMAIN>."
                        ),
                        required=False,
                        example="pod-0199f1c4a2b7712e9d3f5a6b8c0d1e2f@mail.example.com",
                    ),
                ],
                steps=[
                    SurfaceSetupStep(
                        phase=SurfaceSetupPhase.PREPARE,
                        title="Verify the catch-all domain",
                        description=(
                            "Set RESEND_INBOUND_DOMAIN to a domain verified in Resend and configured "
                            "as a catch-all, so *@domain reaches one webhook and no address needs "
                            "registering. Surface creation fails without it."
                        ),
                    ),
                    SurfaceSetupStep(
                        phase=SurfaceSetupPhase.CONFIGURE_PROVIDER,
                        title="Point Resend at Lemma",
                        description=(
                            "Add the inbound webhook in Resend and set RESEND_WEBHOOK_SECRET to its "
                            "Svix signing secret. Lemma rejects unsigned or missigned deliveries."
                        ),
                    ),
                    SurfaceSetupStep(
                        phase=SurfaceSetupPhase.VERIFY,
                        title="Verify reply flow",
                        description="Email the provisioned address and confirm the agent replies on the same thread.",
                    ),
                ],
                notes=[
                    "There is no account_id: this surface runs on system credentials, not a connector account.",
                    "Created automatically with the agent, so it is excluded from adoption metrics — an auto-provisioned mailbox is not somebody connecting a surface.",
                    "The address uses the full 32-char pod id hex, not a prefix, so two pods can never collide and misroute each other's inbound mail.",
                ],
            )
        ],
    )
