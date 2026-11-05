"""``billing.organization_id`` is a per-tenant metric label, so it is confined.

The allowlist admits it for spend attribution. Any instrument recording it
would get a series per organization, so only the usage service may write it.
"""

from __future__ import annotations

from pathlib import Path

from app.core.observability.span_sanitizer import METRIC_ATTRIBUTE_KEYS

APP = Path(__file__).resolve().parents[3]
ALLOWED = {APP / "modules" / "usage" / "services" / "usage_metrics.py"}


def test_only_the_usage_service_writes_the_billing_organization_label() -> None:
    assert "billing.organization_id" in METRIC_ATTRIBUTE_KEYS
    writers = {
        path
        for path in APP.rglob("*.py")
        if "tests" not in path.parts
        and path.name != "span_sanitizer.py"
        and '"billing.organization_id"' in path.read_text()
    }
    assert writers == ALLOWED
