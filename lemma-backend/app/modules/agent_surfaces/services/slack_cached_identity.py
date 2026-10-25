"""A cached Slack sender stands only on Slack's proof of them.

On Slack nothing but a proof names a sender any more: Lemma's own app's
(``slack_workspace_people``, the installer's grant or Sign in with Slack), or a
binding made through the installation itself, which is read before the cache
and never written to it. A profile email -- which a workspace admin can set --
stopped matching (audit M17), and a delivery on a bring-your-own app -- whose
signing secret a person holds -- stopped writing who its sender is (H9).

But the identity cache records who a sender resolved to, never how, and rows
those two wrote before they were refused kept naming people: every message
from that Slack user was still read as the member it was once matched to. So a
cached Slack resolution is honoured only while a proof says the same; one that
none backs is forgotten, and the sender goes through the proof path again on
this message -- which finds nobody, or finds the proof that has appeared
since. Migration 0063 clears the rows already there; this is the same rule at
read time, for a row an older process wrote after it ran.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import (
    ParsedInboundSurfaceEvent,
    SurfacePlatform,
)

logger = get_logger(__name__)


async def forgot_an_unproven_slack_answer(
    cache: Any,
    event: ParsedInboundSurfaceEvent,
    cached: Any,
    *,
    proven: UUID | None,
) -> bool:
    """Whether ``cached`` was a Slack answer no proof backs, now forgotten.

    ``proven`` is who Slack's proof says the sender is (``_slack_proof``), and
    ``cached`` the identity-cache row read for them.
    """
    if event.platform is not SurfacePlatform.SLACK or cached.resolved_user_id is None:
        return False
    if proven == cached.resolved_user_id:
        return False
    await cache.forget_resolution(
        platform=SurfacePlatform.SLACK.value,
        tenant_id=event.tenant_id,
        external_user_id=cached.external_user_id,
        resolved_user_id=cached.resolved_user_id,
    )
    logger.warning(
        "agent_surfaces.identity.unproven_slack_resolution_forgotten.observed",
        tenant_id=event.tenant_id,
    )
    return True


__all__ = ["forgot_an_unproven_slack_answer"]
