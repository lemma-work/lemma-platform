"""Saying so when a provider reports a token count that cannot be true.

The in-process harness does not bill. Every model request it makes goes through
the metered model, which writes a receipt per request; that receipt is the bill.
What is left here is a diagnostic: the run's own usage figures, read once per
attempt, checked for counts no real request produces.

Read defensively: the shape of `usage` is pydantic-ai's, and a field that is not
there counts as zero rather than raising, because a diagnostic must never be
what fails a run.
"""

from __future__ import annotations

from app.core.log.log import get_logger

logger = get_logger(__name__)

#: Beyond this, a reported count is not a large request -- it is a broken one.
#: The largest context window any model currently offers is a few million
#: tokens, so an order of magnitude above that cannot be real.
_IMPLAUSIBLE_TOKEN_COUNT = 20_000_000


def _usage_value(usage: object, field: str) -> int:
    value = getattr(usage, field, 0)
    if value is None:
        return 0
    try:
        return int(value)
    except TypeError, ValueError:
        return 0


def warn_if_implausible_usage(usage: object) -> None:
    """Say so when a reported count cannot be true, in either direction.

    A provider has been seen reporting hundreds of millions of prompt tokens
    for a single request, escalating request over request while its cache-read
    figure stayed sane. Reported rather than clamped: clamping would invent a
    number, and the honest answer is that the provider's is unusable.
    """
    if usage is None:
        return
    for field in ("input_tokens", "output_tokens"):
        value = _usage_value(usage, field)
        if value > _IMPLAUSIBLE_TOKEN_COUNT:
            logger.warning(
                "agent.usage.implausible_provider_count.degraded",
                usage_field=field,
                reported_value=value,
            )
    # The same failure wearing the other face. A request that reached a provider
    # carried a prompt, so no input tokens against a non-zero request count
    # cannot be true either. Streaming makes it reachable: a chunk carrying no
    # usage can map to zeros and erase the figure the provider sent earlier.
    requests = _usage_value(usage, "requests")
    if requests > 0 and _usage_value(usage, "input_tokens") <= 0:
        logger.warning(
            "agent.usage.missing_provider_count.degraded",
            usage_field="input_tokens",
            request_count=requests,
        )
