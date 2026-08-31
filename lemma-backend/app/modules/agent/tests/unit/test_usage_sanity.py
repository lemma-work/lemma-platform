"""Usage figures a provider reports that cannot be true.

Observed in production: one model reported 656 million prompt tokens for a
single request, escalating request over request within one trace while its
cache-read figure stayed sane. These counts are recorded verbatim and feed usage
accounting, so a silent one becomes a billing figure nobody can explain later.
"""

from __future__ import annotations

import pytest

from app.modules.agent.infrastructure.harnesses.pydantic_ai_usage import (
    _IMPLAUSIBLE_TOKEN_COUNT,
    accumulate_usage,
    usage_totals,
)

pytestmark = pytest.mark.unit


class _Usage:
    def __init__(self, **fields: int) -> None:
        for name, value in fields.items():
            setattr(self, name, value)


class TestImplausibleCountsAreReported:
    def test_a_sane_run_says_nothing(self, caplog) -> None:
        with caplog.at_level("WARNING"):
            usage_totals(_Usage(input_tokens=40_000, output_tokens=900), {})

        assert "implausible_provider_count" not in caplog.text

    def test_an_impossible_prompt_count_is_flagged(self, caplog) -> None:
        with caplog.at_level("WARNING"):
            usage_totals(
                _Usage(input_tokens=_IMPLAUSIBLE_TOKEN_COUNT * 30, output_tokens=10), {}
            )

        assert "implausible_provider_count" in caplog.text

    def test_the_number_is_still_returned_not_clamped(self) -> None:
        """Clamping would invent a figure. The honest answer is that the
        provider's is unusable, and that is what gets reported."""
        reported = _IMPLAUSIBLE_TOKEN_COUNT * 30

        totals = usage_totals(_Usage(input_tokens=reported, output_tokens=1), {})

        assert totals["input_tokens"] == reported


class TestCarriedUsageStillAccumulates:
    def test_an_abandoned_attempt_is_still_billed(self) -> None:
        """A mid-stream drop is retried from a snapshot and the provider still
        charges for the attempt that was thrown away."""
        carried: dict[str, int] = {}
        accumulate_usage(carried, _Usage(input_tokens=100, output_tokens=20))

        totals = usage_totals(_Usage(input_tokens=50, output_tokens=5), carried)

        assert totals["input_tokens"] == 150
        assert totals["output_tokens"] == 25

    def test_a_missing_field_counts_as_zero_rather_than_raising(self) -> None:
        """A billing read must never be what fails a run."""
        assert usage_totals(_Usage(input_tokens=10), {})["output_tokens"] == 0


class TestAMissingCountIsReportedToo:
    """The same failure in the other direction, and the more dangerous one.

    A total of zero is not recorded wrong -- it is not recorded at all, because
    the recorder drops a usage record whose token counts are all zero. The run
    then looks identical to one that never happened, so nothing downstream has
    anything to notice.
    """

    def test_requests_without_a_prompt_are_flagged(self, caplog) -> None:
        """A request that reached a provider carried a prompt, so this pair
        cannot both be true."""
        with caplog.at_level("WARNING"):
            usage_totals(_Usage(requests=1, input_tokens=0, output_tokens=0), {})

        assert "missing_provider_count" in caplog.text

    def test_a_run_that_never_reached_the_provider_says_nothing(self, caplog) -> None:
        """No requests and no tokens is consistent -- a run can end before it
        asks the model anything, and warning there would be noise."""
        with caplog.at_level("WARNING"):
            usage_totals(_Usage(requests=0, input_tokens=0, output_tokens=0), {})

        assert "missing_provider_count" not in caplog.text

    def test_a_sane_run_says_nothing(self, caplog) -> None:
        with caplog.at_level("WARNING"):
            usage_totals(_Usage(requests=2, input_tokens=40_000, output_tokens=900), {})

        assert "missing_provider_count" not in caplog.text

    def test_a_carried_prompt_from_an_earlier_attempt_counts(self, caplog) -> None:
        """The attempt that dropped mid-stream already had a prompt billed, so
        the total is not missing anything even when this attempt reports zero."""
        carried: dict[str, int] = {}
        accumulate_usage(carried, _Usage(requests=1, input_tokens=120))

        with caplog.at_level("WARNING"):
            totals = usage_totals(_Usage(requests=1, input_tokens=0), carried)

        assert totals["input_tokens"] == 120
        assert "missing_provider_count" not in caplog.text
