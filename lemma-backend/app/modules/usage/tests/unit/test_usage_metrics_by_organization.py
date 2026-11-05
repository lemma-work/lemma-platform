"""Spend and tokens carry the organization, under a key only spend uses."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.test_support.metric_capture import metric_reader, total
from app.modules.usage.services.usage_metrics import record_usage_metrics


@pytest.fixture(autouse=True)
def _reader() -> None:
    # Before anything records: a point recorded while no provider is
    # installed goes to the no-op meter and is never seen.
    metric_reader()


def test_tokens_and_cost_are_labelled_with_the_organization() -> None:
    org = uuid4()
    record_usage_metrics(
        organization_id=org,
        model_name="m-test",
        usage_kind="llm",
        input_tokens=10,
        output_tokens=3,
        cost_usd=0.5,
    )
    label = {"billing.organization_id": str(org), "gen_ai.request.model": "m-test"}
    assert total("lemma.llm.tokens", **label, **{"gen_ai.token.type": "input"}) == 10
    assert total("lemma.llm.tokens", **label, **{"gen_ai.token.type": "output"}) == 3
    assert total("lemma.llm.cost_usd", **label) == 0.5


def test_usage_with_no_organization_is_labelled_none() -> None:
    before = total("lemma.llm.cost_usd", **{"billing.organization_id": "none"})
    record_usage_metrics(
        organization_id=None,
        model_name="m-test",
        usage_kind="llm",
        input_tokens=0,
        output_tokens=0,
        cost_usd=0.25,
    )
    assert (
        total("lemma.llm.cost_usd", **{"billing.organization_id": "none"})
        == before + 0.25
    )


def test_the_tenant_span_key_still_never_reaches_a_metric() -> None:
    assert total("lemma.llm.cost_usd", **{"lemma.organization_id": "x"}) == 0
