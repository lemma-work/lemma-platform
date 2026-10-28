"""Usage figures a provider reports that cannot be true.

A provider has been seen reporting hundreds of millions of prompt tokens for a
single request, escalating request over request while its cache-read figure
stayed sane. The run's own figures are not the bill -- the metered model's
receipts are -- but they are where a broken count shows first, so it is said.
"""

from __future__ import annotations

import pytest

from app.modules.agent.infrastructure.harness.lemma.usage import (
    _IMPLAUSIBLE_TOKEN_COUNT,
    warn_if_implausible_usage,
)

pytestmark = pytest.mark.unit


class _Usage:
    def __init__(self, **fields: int) -> None:
        for name, value in fields.items():
            setattr(self, name, value)


class TestImplausibleCountsAreReported:
    def test_a_sane_run_says_nothing(self, caplog) -> None:
        with caplog.at_level("WARNING"):
            warn_if_implausible_usage(
                _Usage(requests=2, input_tokens=40_000, output_tokens=900)
            )

        assert "implausible_provider_count" not in caplog.text
        assert "missing_provider_count" not in caplog.text

    def test_an_impossible_prompt_count_is_flagged(self, caplog) -> None:
        with caplog.at_level("WARNING"):
            warn_if_implausible_usage(
                _Usage(input_tokens=_IMPLAUSIBLE_TOKEN_COUNT * 30, output_tokens=10)
            )

        assert "implausible_provider_count" in caplog.text

    def test_a_missing_field_counts_as_zero_rather_than_raising(self, caplog) -> None:
        """A diagnostic must never be what fails a run."""
        with caplog.at_level("WARNING"):
            warn_if_implausible_usage(_Usage(input_tokens=10))
            warn_if_implausible_usage(None)

        assert "implausible_provider_count" not in caplog.text


class TestAMissingCountIsReportedToo:
    """The same failure in the other direction: a request with no prompt."""

    def test_requests_without_a_prompt_are_flagged(self, caplog) -> None:
        with caplog.at_level("WARNING"):
            warn_if_implausible_usage(
                _Usage(requests=1, input_tokens=0, output_tokens=0)
            )

        assert "missing_provider_count" in caplog.text

    def test_a_run_that_never_reached_the_provider_says_nothing(self, caplog) -> None:
        """No requests and no tokens is consistent -- a run can end before it
        asks the model anything, and warning there would be noise."""
        with caplog.at_level("WARNING"):
            warn_if_implausible_usage(
                _Usage(requests=0, input_tokens=0, output_tokens=0)
            )

        assert "missing_provider_count" not in caplog.text
