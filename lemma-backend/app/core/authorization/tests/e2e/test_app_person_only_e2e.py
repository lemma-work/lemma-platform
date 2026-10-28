"""What a pod app's page may not do even as its viewer (H20, ``require_person``).

Inside its own pod an app's page has its viewer's whole access. A few acts are
the person's own whatever the page is allowed: answering their pending agent
approvals, approving a join request. An editor's app opened by an owner would
otherwise approve on the owner's behalf without the owner ever seeing the
question. And the person's own sandbox -- ``/workspace/*``, ``/web-logins`` --
is outside any pod, so an app has no business there at all.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import AsyncClient
from starlette import status

from app.core.api.actor_policy import APP_PERSON_ONLY, APP_ROUTE_NOT_ALLOWED
from app.core.authorization.tests.e2e import test_app_pin_e2e as pin

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

app_hosts = pin.app_hosts
browser = pin.browser
world = pin.world


async def _conversation(browser: AsyncClient, world) -> str:
    created = await browser.post(
        f"/pods/{world['pod_id']}/conversations",
        headers=pin._from_app(world["origin"], world["owner"]),
        json={"agent_runtime": {"profile_id": "system:lemma"}},
    )
    assert created.status_code == status.HTTP_201_CREATED, created.text
    return created.json()["id"]


async def test_an_app_cannot_answer_its_viewers_approval(browser: AsyncClient, world):
    """Refused before the approval is looked up: the app never learns whether
    it named a real one. The viewer, outside the app, reaches the lookup."""
    conversation_id = await _conversation(browser, world)
    path = (
        f"/pods/{world['pod_id']}/conversations/{conversation_id}"
        f"/approvals/call_{uuid4().hex[:8]}/decision"
    )
    body = {"decision": "APPROVE_ONCE"}

    as_app = await browser.post(
        path, headers=pin._from_app(world["origin"], world["owner"]), json=body
    )
    as_owner = await browser.post(path, headers=pin._cookie(world["owner"]), json=body)

    assert as_app.status_code == status.HTTP_403_FORBIDDEN, as_app.text
    assert pin._code(as_app) == APP_PERSON_ONLY, as_app.text
    assert "answer an approval" in as_app.json()["message"]
    assert pin._code(as_owner) != APP_PERSON_ONLY, as_owner.text


async def test_an_app_cannot_approve_a_join_request(browser: AsyncClient, world):
    path = f"/pods/{world['pod_id']}/join-requests/{uuid4()}/approve"
    body = {"org_role": "ORG_MEMBER", "pod_role": "POD_USER"}

    as_app = await browser.post(
        path, headers=pin._from_app(world["origin"], world["owner"]), json=body
    )
    as_owner = await browser.post(path, headers=pin._cookie(world["owner"]), json=body)

    assert as_app.status_code == status.HTTP_403_FORBIDDEN, as_app.text
    assert pin._code(as_app) == APP_PERSON_ONLY, as_app.text
    assert pin._code(as_owner) != APP_PERSON_ONLY, as_owner.text


def _flatten(routes) -> list[object]:
    out: list[object] = []
    for included in routes:
        candidates = getattr(included, "effective_candidates", None)
        if not callable(candidates):
            out.append(included)
            continue
        for candidate in candidates():
            if callable(getattr(candidate, "effective_candidates", None)):
                out.extend(_flatten([candidate]))
            else:
                out.append(getattr(candidate, "route", candidate))
    return out


def _sandbox_routes(test_app) -> list[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for route in _flatten(test_app.routes):
        # A socket has no methods; the browser view's is held by its own test.
        path = getattr(route, "path", "")
        if not path.startswith(("/workspace/", "/web-logins")):
            continue
        for method in getattr(route, "methods", None) or ():
            if method not in {"HEAD", "OPTIONS"}:
                found.add((method, path))
    return sorted(found)


async def test_an_app_reaches_none_of_its_viewers_sandbox_routes(
    test_app, browser: AsyncClient, world
):
    """Outside any pod and not in ``APP_ROUTES``: every one is refused by the
    pin, before its handler runs."""
    routes = _sandbox_routes(test_app)
    assert any(path.startswith("/workspace/") for _m, path in routes)
    assert any(path.startswith("/web-logins") for _m, path in routes)
    headers = pin._from_app(world["origin"], world["owner"])

    wrong = []
    for method, template in routes:
        response = await browser.request(
            method,
            _fill(template),
            headers=headers,
            json={} if method != "GET" else None,
        )
        if pin._code(response) != APP_ROUTE_NOT_ALLOWED:
            wrong.append(f"{method} {template} -> {response.status_code}")
    assert not wrong, "\n".join(wrong)


def _fill(template: str) -> str:
    """A concrete path for ``template``, every parameter a fresh id."""
    out, rest = [], template
    while "{" in rest:
        head, _, tail = rest.partition("{")
        _name, _, rest = tail.partition("}")
        out.extend([head, str(uuid4())])
    out.append(rest)
    return "".join(out)
