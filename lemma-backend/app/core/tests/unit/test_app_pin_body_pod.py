"""A pod named in a body or query is pinned like one in the path (H20)."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.api.actor_policy import APP_ROUTE_NOT_ALLOWED
from app.core.app_pin import AppOnHost, app_pod_for, refuse_other_pod
from app.core.config import settings
from app.core.domain.errors import DomainError

_APP = AppOnHost(app_id=uuid4(), pod_id=uuid4(), organization_id=uuid4(), name="a")


def _connection(app: AppOnHost | None):
    return SimpleNamespace(
        state=SimpleNamespace(app_request=app),
        scope={"method": "POST", "route": SimpleNamespace(path_format="/x")},
    )


def test_a_person_runs_in_the_pod_they_name_or_none() -> None:
    named = uuid4()
    assert app_pod_for(_connection(None), named) == named
    assert app_pod_for(_connection(None), None) is None


def test_an_app_naming_no_pod_or_its_own_runs_in_its_own() -> None:
    assert app_pod_for(_connection(_APP), None) == _APP.pod_id
    assert app_pod_for(_connection(_APP), _APP.pod_id) == _APP.pod_id


def test_an_app_naming_another_pod_is_refused_once_enforced(monkeypatch) -> None:
    monkeypatch.setattr(settings, "app_pin_cross_pod_enforced", True)

    with pytest.raises(DomainError) as refused:
        app_pod_for(_connection(_APP), uuid4())

    assert refused.value.code == APP_ROUTE_NOT_ALLOWED
    assert refused.value.status_code == 403


def test_until_enforced_another_pod_is_observed_and_served(monkeypatch) -> None:
    monkeypatch.setattr(settings, "app_pin_cross_pod_enforced", False)
    other = uuid4()

    assert refuse_other_pod(_connection(_APP), other) is True
    assert app_pod_for(_connection(_APP), other) == other
