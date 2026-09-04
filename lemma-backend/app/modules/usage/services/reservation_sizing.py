"""How much a run holds while it gets as far as its first settlement.

A reservation is not an estimate of what a run will cost -- nothing knows that
before it starts. It covers the window in which a run can spend without the
counters seeing it, and that window is one model request.

Split out of the service for the same reason `limit_windows` was: none of it
touches storage or the limit port, and the service is a file whose length is
ratcheted.
"""

from __future__ import annotations

from app.modules.usage.contracts import ModelPricing
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
