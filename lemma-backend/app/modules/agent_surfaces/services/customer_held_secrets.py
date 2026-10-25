"""Whether a surface's inbound deliveries are signed with a secret its customer holds.

A Slack signature or a WhatsApp ``X-Hub-Signature-256`` proves a delivery came
from whoever holds the signing secret. On Lemma's own app and Lemma's numbers
that is the platform and nobody else. On a bring-your-own app -- an org's own
Slack app that a person made and pasted in, or an org's own WhatsApp Business
app -- a person holds the secret too: whoever brought it, or anyone who can
open the app's settings. They can sign any body they like, naming any sender,
and it verifies. A teammate app Lemma made itself through the manifest flow is
not counted: its install says so (``SurfaceAuthConfig.made_by_lemma``), and the
surface reads it through its pod connector, so pointing that connector at a
different app takes the answer with it.

So on such a surface the sender id is the secret holder's word, and identity
resolution has to read it that way (``SurfaceIdentityResolutionService``).

Telegram and Teams are not here, on purpose. A Telegram bot's webhook is
checked against the ``secret_token`` Lemma minted for the surface, which the
bot's owner never sees; a Teams activity is a JWT signed by Microsoft's keys,
which no app password can produce.
"""

from __future__ import annotations

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfacePlatform,
)
from app.modules.connectors.contracts import AuthConfigSource
from app.modules.connectors.contracts.pod_connectors import (
    PodConnectorRefusal,
    resolve_standing_pod_connector,
)
from app.modules.connectors.contracts.surfaces import auth_config


def customer_holds_the_secret(
    platform: SurfacePlatform,
    *,
    runs_as_connector: bool,
    config_source: str | None,
    app_made_by_lemma: bool,
) -> bool:
    """The decision, over the facts that make it.

    Mirrors how the webhook is verified rather than how the surface was
    labelled: WhatsApp checks any surface on a connector against the account's
    own ``app_secret`` (``_resolve_whatsapp_secrets``), and Slack checks one
    against the org's app's signing secret exactly when the install is the
    org's own (``slack_webhook_credentials``). Surfaces on Lemma's own
    credentials, pooled numbers among them, are Lemma's.

    ``config_source`` is the install the connector's account was connected
    through; ``None`` is no install of the org's, which is Lemma's own app.
    """
    if not runs_as_connector:
        return False
    if platform is SurfacePlatform.WHATSAPP:
        return True
    if platform is not SurfacePlatform.SLACK:
        return False
    if str(config_source or "").upper() != AuthConfigSource.ORG_CUSTOM.value:
        return False
    # A teammate's app Lemma made through the manifest flow is stored as the
    # org's own install too, but its secret came from Slack to Lemma, not from
    # a person who chose it -- and those apps are how a workspace Lemma holds
    # reaches its pods, where Slack's proof of a member has to keep naming them
    # (PS-SURF-053).
    return not app_made_by_lemma


async def signed_with_a_customer_secret(
    uow: SqlAlchemyUnitOfWork, surface: AgentSurfaceEntity | None
) -> bool:
    """Does ``surface`` verify its deliveries with a secret a person holds?

    The facts for ``customer_holds_the_secret``, read through the surface's pod
    connector. Only a Slack surface needs the install: no other platform's
    answer depends on it.
    """
    if surface is None or surface.pod_connector_slug is None:
        return False
    config_source, made_by_lemma = None, False
    if surface.surface_type is SurfacePlatform.SLACK:
        try:
            connected = await resolve_standing_pod_connector(
                uow,
                pod_id=surface.pod_id,
                slug=surface.pod_connector_slug,
                record_use=False,
            )
        except PodConnectorRefusal:
            # Verification finds no signing secret for it either, so nothing
            # signed reaches here through it; the answer only has to be safe.
            return True
        install = await auth_config(uow, connected.auth_config_id)
        if install is not None:
            config_source, made_by_lemma = install.config_source, install.made_by_lemma
    return customer_holds_the_secret(
        surface.surface_type,
        runs_as_connector=True,
        config_source=config_source,
        app_made_by_lemma=made_by_lemma,
    )
