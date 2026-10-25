"""How a request from a pod app's page is recognised and pinned (H20).

The database half -- which app a slug names -- is the e2e suite's
(``core/tests/e2e/test_app_pin_e2e.py``). These are the decisions made before
and after it: which host a request names, the null-origin refusal, and which
routes and ids a recognised app may reach.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from starlette.requests import Request

from app.core.api.actor_policy import APP_ROUTE_NOT_ALLOWED
from app.core.app_pin import (
    ORIGIN_NULL_NOT_ALLOWED,
    AppOnHost,
    _require_app_route,
    app_host_slug,
    pin_app_request,
)
from app.core.config import settings
from app.core.domain.errors import DomainError

BASE = "apps.pin.test"


@pytest.fixture(autouse=True)
def app_domain(monkeypatch):
    monkeypatch.setattr(settings, "app_base_domain", BASE)


def _request(
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    template: str | None = None,
    path_params: dict[str, str] | None = None,
) -> Request:
    scope = {
        "type": "http",
        "method": method,
        "path": "/",
        "headers": [
            (key.lower().encode(), value.encode())
            for key, value in (headers or {}).items()
        ],
        "path_params": path_params or {},
    }
    if template is not None:
        scope["route"] = SimpleNamespace(path_format=template)
    return Request(scope)


def _app() -> AppOnHost:
    return AppOnHost(
        app_id=uuid4(), pod_id=uuid4(), organization_id=uuid4(), name="orders"
    )


def test_the_origin_names_the_app():
    request = _request(headers={"origin": f"https://orders.{BASE}", "host": "api"})

    assert app_host_slug(request) == "orders"


def test_a_preview_host_is_the_same_app():
    request = _request(headers={"origin": f"https://orders--r7.{BASE}"})

    assert app_host_slug(request) == "orders"


def test_the_host_names_the_app_through_its_own_door():
    request = _request(headers={"host": f"orders.{BASE}"})

    assert app_host_slug(request) == "orders"


@pytest.mark.parametrize(
    "headers",
    [
        {"origin": "https://app.lemma.test", "host": "api.lemma.test"},
        {"origin": f"https://{BASE}"},
        {"origin": f"https://a.b.{BASE}"},
        {"origin": "http://["},
        {},
    ],
)
def test_other_hosts_name_no_app(headers):
    assert app_host_slug(_request(headers=headers)) is None


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_a_null_origin_changes_nothing(method: str):
    """Not read by its Host: an opaque document names no host at all."""
    request = _request(
        method=method,
        headers={"origin": "null", "host": f"orders.{BASE}"},
        template="/pods/{pod_id}/datastore/tables",
    )

    with pytest.raises(DomainError) as refused:
        await pin_app_request(request)

    assert refused.value.code == ORIGIN_NULL_NOT_ALLOWED
    assert refused.value.status_code == 403


def test_an_app_reaches_its_own_pod():
    app = _app()
    request = _request(
        method="DELETE",
        template="/pods/{pod_id}/datastore/tables/{table_name}",
        path_params={"pod_id": str(app.pod_id), "table_name": "t"},
    )

    _require_app_route(request, app)


@pytest.mark.parametrize(
    "pod_id", ["01a16e3d-0000-7000-8000-000000000001", "not-a-uuid"]
)
def test_an_app_is_refused_another_pod(pod_id: str, monkeypatch):
    monkeypatch.setattr(settings, "app_pin_cross_pod_enforced", True)
    request = _request(
        template="/pods/{pod_id}/datastore/tables", path_params={"pod_id": pod_id}
    )

    with pytest.raises(DomainError) as refused:
        _require_app_route(request, _app())

    assert refused.value.code == APP_ROUTE_NOT_ALLOWED
    assert refused.value.details["route"] == "/pods/{pod_id}/datastore/tables"


def test_another_pod_is_served_and_recorded_until_enforcement_is_on(monkeypatch):
    """On main an app could name any pod. Until the refusal is switched on, a
    call to another pod is served as before and logged, so deployed apps
    relying on it show up first."""
    monkeypatch.setattr(settings, "app_pin_cross_pod_enforced", False)
    request = _request(
        template="/pods/{pod_id}/datastore/tables",
        path_params={"pod_id": "01a16e3d-0000-7000-8000-000000000001"},
    )

    _require_app_route(request, _app())


def test_an_app_invites_only_as_a_member_of_its_own_pod():
    from app.core.app_pin import refuse_app_invitation_above_member

    app = _app()
    request = SimpleNamespace(state=SimpleNamespace(app_request=app))
    refuse_app_invitation_above_member(
        request, role="ORG_MEMBER", pod_id=app.pod_id, pod_role="POD_USER"
    )
    for role, pod_id, pod_role in (
        ("ORG_OWNER", None, None),
        ("ORG_EDITOR", None, None),
        ("ORG_MEMBER", app.pod_id, "POD_ADMIN"),
        ("ORG_MEMBER", uuid4(), "POD_USER"),
    ):
        with pytest.raises(DomainError) as refused:
            refuse_app_invitation_above_member(
                request, role=role, pod_id=pod_id, pod_role=pod_role
            )
        assert refused.value.code == APP_ROUTE_NOT_ALLOWED


def test_an_app_sets_connectors_up_in_its_own_organization_only():
    app = _app()
    template = "/organizations/{organization_id}/connectors/auth-configs"
    own = _request(
        method="POST",
        template=template,
        path_params={"organization_id": str(app.organization_id)},
    )
    other = _request(
        method="POST", template=template, path_params={"organization_id": str(uuid4())}
    )

    _require_app_route(own, app)
    with pytest.raises(DomainError) as refused:
        _require_app_route(other, app)
    assert refused.value.code == APP_ROUTE_NOT_ALLOWED


@pytest.mark.parametrize(
    ("method", "template"),
    [
        ("PATCH", "/organizations/{organization_id}"),
        ("POST", "/pods"),
        ("GET", None),
    ],
)
def test_an_app_is_refused_everything_else_by_name(method: str, template):
    app = _app()
    request = _request(
        method=method,
        template=template,
        path_params={"organization_id": str(app.organization_id)},
    )

    with pytest.raises(DomainError) as refused:
        _require_app_route(request, app)

    assert refused.value.code == APP_ROUTE_NOT_ALLOWED
    assert refused.value.status_code == 403
    assert method in refused.value.message


def test_a_renamed_or_deleted_apps_slug_is_forgotten_once_the_change_commits():
    """Otherwise the old slug keeps naming the app -- or, once another app
    takes it, names the wrong app and pod -- until the cache expires."""
    from app.core.app_pin import forget_app_host_after_commit

    registered: list = []

    class _Uow:
        def after_commit(self, callback):
            registered.append(callback)

    class _Repository:
        uow = _Uow()

    forget_app_host_after_commit(_Repository(), "old-slug")
    forget_app_host_after_commit(_Repository(), None)

    assert len(registered) == 1
