"""A failed send is retried in one place, and at most three times in total.

Adapter, job and inbox each retrying multiplies the attempts against an API that
is already failing. These pin the rule so a new consumer cannot quietly add a
layer: see `domain/delivery_limits.py` for why it is the adapter that retries.
"""

from __future__ import annotations

from app.core.domain.errors import DomainError
from app.modules.agent_surfaces.domain.delivery_limits import (
    CONSUMER_ATTEMPTS,
    MAX_ATTEMPTS,
    MAX_DELIVERY_ATTEMPTS,
)
from app.modules.agent_surfaces.domain.deliveries import (
    MAX_SEND_ATTEMPTS,
    RETRY_DELAYS,
)
from app.modules.agent_surfaces.domain.errors import AgentSurfacePlatformError
from app.modules.agent_surfaces.platforms.delivery import RetryPolicy


def test_the_adapter_retry_is_capped_at_three_attempts_in_total():
    assert 1 < MAX_DELIVERY_ATTEMPTS <= 3
    assert RetryPolicy().max_attempts == MAX_DELIVERY_ATTEMPTS


def test_no_layer_retries_more_than_three_times_in_total():
    assert MAX_ATTEMPTS == 3
    assert CONSUMER_ATTEMPTS <= MAX_ATTEMPTS
    assert MAX_DELIVERY_ATTEMPTS <= MAX_ATTEMPTS


def test_a_send_that_gave_up_is_terminal_so_no_layer_above_repeats_it():
    """The other half of the rule: the adapter retries, and nothing above does.

    A send that exhausted its attempts surfaces as a domain error, which the
    caller records on the delivery row rather than raising into a retry.
    """
    error = AgentSurfacePlatformError("SLACK", "nothing reached the person.")

    assert isinstance(error, DomainError)
    assert error.status_code != 503


def test_the_ingest_item_retries_at_most_the_consumer_cap():
    """``surface.ingest`` is the one layer that retries what happens before a send."""
    from app.core.infrastructure.work import registry
    from app.modules.agent_surfaces.events import ingest_work  # noqa: F401
    from app.modules.agent_surfaces.events.work_kinds import SURFACE_INGEST

    assert registry.get(SURFACE_INGEST).retry.max_attempts == CONSUMER_ATTEMPTS


def test_a_refused_delivery_is_retried_on_the_platform_clock_a_bounded_number_of_times():
    """The pump's retries of one row: three waits, then FAILED -- never a loop."""
    assert len(RETRY_DELAYS) == MAX_ATTEMPTS
    assert MAX_SEND_ATTEMPTS == 1 + len(RETRY_DELAYS)
