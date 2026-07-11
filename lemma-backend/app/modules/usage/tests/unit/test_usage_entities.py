from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.modules.usage.domain.entities import (
    UsageKind,
    UsageProfileScope,
    UsageRecord,
    UsageSummary,
    _as_float,
    _as_int,
)


def _summary() -> UsageSummary:
    now = datetime.now(timezone.utc)
    return UsageSummary(start_date=now, end_date=now, period_days=1)


def _record(**updates) -> UsageRecord:
    values = {
        "user_id": uuid4(),
        "profile_id": "system-default",
        "profile_scope": UsageProfileScope.SYSTEM,
        "model_name": "model-a",
        "usage_kind": UsageKind.LLM,
        "input_tokens": 10,
        "output_tokens": 5,
        "units": 2.5,
        "cost_usd": 0.25,
    }
    values.update(updates)
    return UsageRecord(**values)


def test_summary_accumulates_all_dimensions_and_numeric_bucket_values():
    summary = _summary()

    summary.add_usage(_record())
    summary.add_usage(
        _record(
            input_tokens=3,
            output_tokens=2,
            units=1.0,
            cost_usd=None,
            usage_kind="CUSTOM",
        )
    )

    assert summary.total_tokens == 20
    assert summary.total_units == 3.5
    assert summary.system_cost_usd == 0.25
    assert summary.total_by_profile["system-default"] == {
        "input_tokens": 13,
        "output_tokens": 7,
        "total_tokens": 20,
        "units": 3.5,
        "system_cost_usd": 0.25,
        "record_count": 2,
    }
    assert summary.total_by_model["model-a"]["record_count"] == 2
    assert summary.total_by_kind["LLM"]["record_count"] == 1
    assert summary.total_by_kind["CUSTOM"]["record_count"] == 1


def test_numeric_bucket_converters_accept_scalars_and_reject_other_values():
    assert _as_int("2") == 2
    assert _as_int(2.9) == 2
    assert _as_float("2.5") == 2.5

    with pytest.raises(TypeError, match="not numeric"):
        _as_int(object())
    with pytest.raises(TypeError, match="not numeric"):
        _as_float(object())
