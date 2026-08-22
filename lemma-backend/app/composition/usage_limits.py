"""The configuration-backed :class:`UsageLimitPort`.

A deployment can now state its own spend limits in settings, without shipping a
billing module: a deployment-wide monthly ceiling per organization (and per
user), with per-organization overrides keyed by slug. Work that would exceed a
configured limit is refused and says which limit was reached — that is the whole
of PS-OPS-012, which until this existed could never run anywhere: there was no
way to configure a limit at all (DEV-OPS-004).

A billing/plan provider still wins when registered via
``configure_usage_limit_provider``; this port is what a deployment gets when
nothing else supplies one.
"""

from __future__ import annotations

import json
from uuid import UUID

from app.core.config import settings
from app.core.log.log import get_logger
from app.modules.usage.domain.ports import UsageLimitPort, UsageLimitValues

logger = get_logger(__name__)


def _parse_overrides(raw: str) -> list[tuple[str, float, bool]]:
    """Override rules from the overrides JSON, in order.

    Each entry carries either ``slug`` (exact handle) or ``slug_prefix`` (a
    handle prefix, so a family of organizations shares one cap), plus
    ``monthly_limit_usd``. Malformed input yields nothing rather than taking
    spending decisions down with it -- but it warns, because a spend cap that
    silently does not apply is worse than one that refuses to start.
    """
    if not raw.strip():
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(
            "usage.limit_overrides.unparseable",
            detail="USAGE_ORG_LIMIT_OVERRIDES_JSON is not valid JSON; "
            "per-organization spend caps are NOT in effect",
        )
        return []
    if not isinstance(parsed, list):
        logger.warning(
            "usage.limit_overrides.not_a_list",
            detail="USAGE_ORG_LIMIT_OVERRIDES_JSON must be a JSON list; "
            "per-organization spend caps are NOT in effect",
        )
        return []
    rules: list[tuple[str, float, bool]] = []
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        limit = entry.get("monthly_limit_usd")
        if not isinstance(limit, (int, float)):
            continue
        slug = entry.get("slug")
        prefix = entry.get("slug_prefix")
        if isinstance(slug, str) and slug:
            rules.append((slug, float(limit), False))
        elif isinstance(prefix, str) and prefix:
            rules.append((prefix, float(limit), True))
    return rules


def _limit_for(slug: str | None, rules: list[tuple[str, float, bool]]) -> float | None:
    """The limit from the last matching rule, or ``None`` if none match.

    Last wins rather than first so a specific ``slug`` written after a broad
    ``slug_prefix`` overrides it, which is the order people write these in.
    """
    if slug is None:
        return None
    matched: float | None = None
    for value, limit, is_prefix in rules:
        if slug.startswith(value) if is_prefix else slug == value:
            matched = limit
    return matched


class ConfiguredUsageLimitPort:
    """Limits read from settings, resolved per request against the org's slug."""

    def __init__(self, uow: object):
        self._uow = uow

    async def resolve_limits(
        self,
        *,
        organization_id: UUID | None,
        user_id: UUID,
    ) -> UsageLimitValues | None:
        org_monthly = settings.usage_org_monthly_limit_usd

        rules = _parse_overrides(settings.usage_org_limit_overrides_json)
        if organization_id is not None and rules:
            from app.modules.identity.infrastructure.organization_repositories import (
                OrganizationRepository,
            )

            organization = await OrganizationRepository(self._uow).get(organization_id)
            if organization is not None:
                override = _limit_for(organization.slug, rules)
                if override is not None:
                    org_monthly = override

        values = UsageLimitValues(
            org_monthly_limit_usd=org_monthly,
            user_weekly_limit_usd=settings.usage_user_weekly_limit_usd,
            user_monthly_limit_usd=settings.usage_user_monthly_limit_usd,
        )
        has_any = (
            values.org_monthly_limit_usd is not None
            or values.user_weekly_limit_usd is not None
            or values.user_monthly_limit_usd is not None
        )
        return values if has_any else None


def configured_usage_limit_port(uow: object) -> UsageLimitPort | None:
    """The settings-backed port when any limit is configured, else ``None``."""
    has_any = (
        settings.usage_org_monthly_limit_usd is not None
        or settings.usage_user_weekly_limit_usd is not None
        or settings.usage_user_monthly_limit_usd is not None
        or bool(settings.usage_org_limit_overrides_json.strip())
    )
    if not has_any:
        return None
    return ConfiguredUsageLimitPort(uow)
