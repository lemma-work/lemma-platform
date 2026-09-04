"""How much a run holds while it gets as far as its first settlement.

A reservation is not an estimate of what a run will cost -- nothing knows that
before it starts. It covers the window in which a run can spend without the
counters seeing it, and that window is one model request.

Split out of the service for the same reason `limit_windows` was: none of it
touches storage or the limit port, and the service is a file whose length is
ratcheted.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.modules.usage.contracts import ModelPricing
from app.modules.usage.domain.events import UsageLimitApproachingEvent
from app.modules.usage.services.cost_resolver import UsageTokens, resolve_cost

#: The request a reservation assumes. Declared as a contract rather than
#: measured from traffic: it is the size this deployment is willing to be wrong
#: by for one request, and it is deliberately generous on input because
#: under-reserving is what lets many simultaneous admissions collectively pass a
#: limit each of them individually fitted inside.
RESERVED_REQUEST = UsageTokens(input_tokens=100_000, output_tokens=4_000)

#: The three windows a run can be held by, in the order a reader expects them.
_SCOPES = ("org_monthly", "user_weekly", "user_monthly")


def reservation_amount(
    *,
    model_name: str,
    pricing_table: dict[str, ModelPricing],
    floor: float,
) -> float:
    """One nominal request on this model, or ``floor`` if nothing prices it.

    A flat few cents was the whole hold however expensive the model, so a
    hundred runs admitted at once against one allowance could each go on to buy
    a request nothing had accounted for. Pricing the same nominal request on the
    model actually chosen makes admission mean something on an expensive one
    without punishing a cheap one.

    Falling back rather than refusing is `PS-OPS-011`: a gap in the pricing
    table must never stop work, so an unpriceable model still takes an admission
    token -- small, because it stands in for a number nobody could compute
    rather than estimating anything.
    """
    priced = resolve_cost(
        model_name=model_name,
        provider_model_name=None,
        base_url=None,
        tokens=RESERVED_REQUEST,
        pricing_table=pricing_table,
    ).cost_usd
    if not priced:
        return floor
    return max(floor, round(priced, 8))


def remaining_after(limits: dict[str, object], reserved: float) -> float | None:
    """What this run may still spend, once its own hold is accounted for.

    The binding constraint is whichever window runs out first, so a run bounds
    itself by the minimum -- not by the organization's monthly figure that a
    weekly per-user cap will stop it reaching.

    ``reserved`` is subtracted because these figures were read *before* the
    reservation was taken. Leaving it in overstated the allowance by the hold
    the run is itself carrying, and -- more importantly -- a run starting a
    moment later reads the same windows with this run's hold already in
    ``reserved_usd``, so the two no longer believe they each have the whole
    remainder to themselves.
    """
    remaining = [
        scope["remaining_usd"]
        for key in _SCOPES
        for scope in [limits[key]]
        if isinstance(scope, dict) and scope.get("remaining_usd") is not None
    ]
    if not remaining:
        return None
    return max(0.0, min(remaining) - reserved)


def approaching_events(
    limits: dict[str, object],
    *,
    reserved: float,
    fraction: float,
    user_id: UUID,
) -> list[UsageLimitApproachingEvent]:
    """Every window this reservation just carried past its warning line.

    Usually none, occasionally one, and more than one only when two windows sit
    at almost the same fraction -- which is somebody genuinely running out of
    two allowances at once, and worth saying twice.
    """
    if fraction <= 0:
        return []
    events = []
    for key in _SCOPES:
        scope = limits.get(key)
        if not isinstance(scope, dict):
            continue
        event = _approaching_event(
            scope, key=key, reserved=reserved, fraction=fraction, user_id=user_id
        )
        if event is not None:
            events.append(event)
    return events


def _approaching_event(
    scope: dict[str, object],
    *,
    key: str,
    reserved: float,
    fraction: float,
    user_id: UUID,
) -> UsageLimitApproachingEvent | None:
    """The warning this reservation just triggered, if it triggered one.

    ``None`` unless the hold being placed is what carries this window past the
    threshold. The figures in ``scope`` were read before the hold, so the pair
    (before, after) straddles the line exactly once per window -- which is how
    one warning is emitted rather than one per run from then on.
    """
    limit = scope.get("limit_usd")
    if not isinstance(limit, (int, float)) or limit <= 0:
        return None
    before = _as_float(scope.get("used_usd")) + _as_float(scope.get("reserved_usd"))
    after = before + reserved
    threshold = limit * fraction
    if before >= threshold or after < threshold:
        return None
    reset_at = scope.get("reset_at")
    window_start = scope.get("window_start")
    if not isinstance(reset_at, datetime) or not isinstance(window_start, datetime):
        return None
    counter_organization_id = scope.get("counter_organization_id")
    return UsageLimitApproachingEvent(
        organization_id=(
            counter_organization_id
            if isinstance(counter_organization_id, UUID)
            else None
        ),
        user_id=user_id,
        scope=key,
        window_start=window_start,
        reset_at=reset_at,
        limit_usd=float(limit),
        consumed_usd=after,
        threshold_fraction=fraction,
    )


def _as_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)
