"""Keeping Agent Host profiles in step with what their harness publishes.

A profile copies its harness's model list into ``model_catalog`` when it is
saved. Nothing rebuilt that copy afterwards, so a coding agent that gained a
model kept offering the old list in every picker, and one that dropped a model
kept a profile pinned to it -- refused at every turn as unavailable until
somebody happened to open the profile and press Save.

So every publish rewrites what the machine owns on the profiles of the
harnesses it reported: the model list and the harness revision. What a person
chose is left alone. A pinned model the harness stopped offering stays pinned,
and the next turn on it is refused with that reason -- the rule
``selected_model`` keeps, because swapping a pin for the harness default ran a
teammate on another model with nobody told. A selection the model stopped
offering stays saved too, and dispatch drops it from the run it no longer
fits. Nothing is refused here: a publish is news about the machine, not a
request anybody can correct.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.log.log import get_logger
from app.modules.agent.domain.runtime_profiles import HarnessRuntimeConfig
from app.modules.agent.agent_host.control.harness_profiles import (
    agent_host_profiles_for_update,
)
from app.modules.agent.infrastructure.runtime_models import (
    AgentHostHarnessModel,
    AgentRuntimeProfileModel,
)
from app.modules.agent.services.runtime_system_profiles import (
    agent_host_model_catalog,
)

logger = get_logger(__name__)


async def refresh_harness_profiles(
    session: AsyncSession, harnesses: Sequence[AgentHostHarnessModel]
) -> None:
    """Rewrite each profile on ``harnesses`` that the publish changed.

    In the publishing transaction, so the profiles and the harness they
    describe are never seen disagreeing. A profile the publish leaves as it was
    is not written, so its version -- which an edit open in somebody's browser
    was read at -- does not move for nothing.
    """
    by_id = {harness.id: harness for harness in harnesses}
    for profile in await agent_host_profiles_for_update(session, by_id.keys()):
        harness = by_id.get(profile.harness_id) if profile.harness_id else None
        if harness is not None:
            _refresh_profile(profile, harness)
    await session.flush()


def _refresh_profile(
    profile: AgentRuntimeProfileModel, harness: AgentHostHarnessModel
) -> None:
    try:
        stored = HarnessRuntimeConfig.model_validate(profile.config or {})
    except ValidationError:
        # Left exactly as it is: reading it is refused with its own reason
        # (`RuntimeProfileUnreadableError`), and a rewrite from here would
        # replace whatever is wrong with it by a guess.
        logger.warning(
            "agent.agent_host_profile_refresh.unreadable_profile_skipped.degraded",
            profile_id=str(profile.id),
            harness_id=str(harness.id),
        )
        return
    config_options = list(harness.config_options or [])
    catalog = [
        entry.model_dump(mode="json")
        for entry in agent_host_model_catalog(
            config_options,
            supports_images=(harness.capabilities or {}).get("images") is True,
        )
    ]
    config = stored.model_copy(
        update={"harness_snapshot_revision": harness.config_revision}
    ).model_dump(mode="json")
    if catalog == list(profile.model_catalog or []) and config == dict(
        profile.config or {}
    ):
        return
    offered = {entry["name"] for entry in catalog}
    logger.info(
        "agent.agent_host_profile_refresh.profile_refreshed",
        profile_id=str(profile.id),
        harness_id=str(harness.id),
        pinned_model_withdrawn=profile.default_model_name is not None
        and profile.default_model_name not in offered,
    )
    # Fresh containers, so the JSONB columns are seen as changed.
    profile.model_catalog = catalog
    profile.config = config
