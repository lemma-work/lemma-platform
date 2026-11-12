"""The route policy names real routes, and keeps credential routes closed.

``WORKLOAD_ROUTES`` is the whole list of routes outside a pod a workload's token
may call. An entry that matches no route opens nothing today and silently opens
whatever is later added at that path, so every entry must name a route that
exists. And the routes that hand out credentials -- the reason the policy
exists -- must stay off it whatever else is added.
"""

from __future__ import annotations

import pytest

from app.core.api.actor_policy import WORKLOAD_ROUTES, workload_may_call


def _app_routes() -> set[tuple[str, str]]:
    from app.app import app

    found: set[tuple[str, str]] = set()
    for included in app.routes:
        # Included routers resolve to their routes lazily; a plain route is its
        # own only candidate.
        candidates = getattr(included, "effective_candidates", None)
        routes = candidates() if callable(candidates) else [included]
        for candidate in routes:
            route = getattr(candidate, "route", candidate)
            path = getattr(route, "path", None)
            for method in getattr(route, "methods", None) or ():
                found.add((method, path))
    return found


def test_every_open_route_exists():
    missing = WORKLOAD_ROUTES - _app_routes()
    assert not missing, f"WORKLOAD_ROUTES names routes that do not exist: {missing}"


@pytest.mark.parametrize(
    ("method", "template"),
    [
        ("POST", "/auth/cli/session-tokens"),
        ("POST", "/auth/desktop/requests/{request_id}/complete"),
        ("GET", "/oauth/consent/{request_id}"),
        ("POST", "/oauth/consent/{request_id}"),
        ("GET", "/oauth/grants"),
        ("POST", "/organizations/{organization_id}/invitations"),
        ("PATCH", "/organizations/{organization_id}"),
        ("DELETE", "/organizations/{organization_id}/connectors/accounts/{account_id}"),
        ("POST", "/organizations/{organization_id}/connectors/auth-configs"),
        ("PUT", "/usage/organizations/{organization_id}/contacts-cap"),
        ("POST", "/pods"),
        ("GET", "/web-logins"),
    ],
)
def test_routes_that_act_as_the_person_stay_closed(method: str, template: str):
    assert not workload_may_call(method, template)


@pytest.mark.parametrize(
    "template",
    ["/pods/{pod_id}", "/pods/{pod_id}/agents", "/pods/{pod_id}/datastore/query"],
)
def test_pod_routes_are_open(template: str):
    assert workload_may_call("GET", template)
    assert workload_may_call("POST", template)


def test_a_prefix_that_only_looks_like_a_pod_route_is_not():
    assert not workload_may_call("GET", "/pods/{pod_id}x/agents")
    assert not workload_may_call("POST", "/pods/bundle/download")


# --- H20: what a pod app's page may call outside its own pod ---


def test_every_app_route_exists():
    from app.core.api.actor_policy import APP_ROUTES

    missing = APP_ROUTES - _app_routes()
    assert not missing, f"APP_ROUTES names routes that do not exist: {missing}"


@pytest.mark.parametrize(
    ("method", "template"),
    [
        ("PATCH", "/organizations/{organization_id}"),
        (
            "DELETE",
            "/organizations/{organization_id}/connectors/auth-configs/{auth_config_name}",
        ),
        # Never what already exists for other people (review of #1059): an
        # installation every member's credentials go through, a role, a seat.
        (
            "PATCH",
            "/organizations/{organization_id}/connectors/auth-configs/{auth_config_name}",
        ),
        (
            "POST",
            (
                "/organizations/{organization_id}/connectors/auth-configs"
                "/{auth_config_name}/operations/refresh"
            ),
        ),
        (
            "DELETE",
            "/organizations/{organization_id}/connectors/accounts/{account_id}",
        ),
        ("PATCH", "/organizations/{organization_id}/members/{member_id}/role"),
        ("DELETE", "/organizations/{organization_id}/members/{member_id}"),
        ("PUT", "/usage/organizations/{organization_id}/contacts-cap"),
        ("POST", "/users/me/profile"),
        ("POST", "/pods"),
        ("POST", "/auth/cli/session-tokens"),
        ("GET", "/oauth/grants"),
    ],
)
def test_an_app_may_not_call_org_settings_or_credentials(method: str, template: str):
    from app.core.api.actor_policy import app_may_call

    assert not app_may_call(method, template)


def test_an_app_may_call_its_pod_and_connector_setup():
    from app.core.api.actor_policy import app_may_call

    assert app_may_call("DELETE", "/pods/{pod_id}/datastore/tables/{table_name}")
    assert app_may_call("GET", "/users/me")
    assert app_may_call(
        "POST", "/organizations/{organization_id}/connectors/auth-configs"
    )
    assert app_may_call("POST", "/organizations/{organization_id}/invitations")
    assert not app_may_call("POST", "/pods/bundle/download")
