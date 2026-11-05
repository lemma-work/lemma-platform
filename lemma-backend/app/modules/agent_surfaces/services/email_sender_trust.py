"""Whether an inbound email's ``From:`` may name a Lemma user at all.

Asked above the external-user cache in identity resolution, and kept apart from
it because it is a rule about mail authentication, not about matching.
"""

from __future__ import annotations

from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import ParsedInboundSurfaceEvent

logger = get_logger(__name__)


def email_sender_is_believable(event: ParsedInboundSurfaceEvent) -> bool:
    """May this inbound email's ``From:`` be resolved to a Lemma user?

    Only email is asked. Every chat platform asserts its sender inside a payload
    whose signature was already verified, so there is nothing here to doubt --
    ``sender_authentication`` is None for them and this passes.

    **There is no setting.** There used to be one, defaulting to "believe an
    unauthenticated ``From:``", on the reasoning that not every provider writes
    ``Authentication-Results`` and refusing blind would stop resolving every
    inbound sender. That reasoning rested on a question nobody had asked the
    provider: 20 of 20 real inbound messages carry the header, written by
    ``amazonses.com``, because Resend receives through SES. So the config was
    protecting against something that does not happen, at the price of leaving
    account takeover a single environment variable away -- and a flag whose safe
    value is the default is not a flag, it is an unexploited hole.

    What is left is the rule ``PS-SURF-022`` already stated and the code did not
    keep: a sender the receiving mail service did not vouch for does not become
    a member. They are a stranger, and the unresolved-sender path tells them how
    to get access -- which is what should happen to someone we cannot identify.

    ``None`` on an email surface is UNKNOWN, not "nothing to check". Only
    ``merge_received_email`` ever sets the verdict, so it is absent whenever
    enrichment did not run: no ``email_id`` on the webhook, an ``HTTPError`` on
    the body fetch, or the whole polling receiver. Reading that as the chat
    platforms' "no question to ask" meant an attacker who could make the fetch
    fail -- or a deployment in polling mode -- skipped this check entirely.
    """
    from app.modules.agent_surfaces.platforms.email_authentication import (
        EmailAuthenticationVerdict,
    )

    verdict = event.sender_authentication
    if verdict is None and not event.platform.is_email:
        return True
    if verdict == EmailAuthenticationVerdict.PASS:
        return True
    if verdict == EmailAuthenticationVerdict.FAIL:
        logger.warning(
            "agent_surfaces.identity.email_sender_failed_authentication.degraded",
            platform=str(event.platform),
            sender_email=event.sender_email,
        )
        return False
    # UNKNOWN. Nothing vouched for this address, so it names nobody here.
    logger.warning(
        "agent_surfaces.identity.email_sender_unauthenticated.degraded",
        platform=str(event.platform),
        sender_email=event.sender_email,
        resolved=False,
    )
    return False
