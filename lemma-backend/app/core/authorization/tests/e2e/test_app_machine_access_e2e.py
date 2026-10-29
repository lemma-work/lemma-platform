"""An app's page on its viewer's own machine: refused until the viewer says yes.

The one exception to H20's "an app has no business in its viewer's sandbox"
(``test_app_person_only_e2e``): an app may run code on its viewer's default
machine, and only after that viewer granted it -- in Lemma, never from the
app's own page. Granted, it reaches the machine's processes, files and
previews and nothing else: the browser, its logins and resetting the machine
stay the person's own. Revoked, it is refused again.

Nothing here starts a machine: every route asked is answered from rows
(`previews`) or refused before a sandbox would be reached.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from starlette import status

from app.core.api.actor_policy import APP_ROUTE_NOT_ALLOWED
from app.core.authorization.tests.e2e import test_app_pin_e2e as pin

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

app_hosts = pin.app_hosts
browser = pin.browser
world = pin.world


async def _app_id(browser: AsyncClient, world) -> str:
    """The app's own id, as the refusal names it to the page."""
    refused = await browser.get(
        "/machines/me/previews",
        headers=pin._from_app(world["origin"], world["owner"]),
    )
    assert refused.status_code == status.HTTP_403_FORBIDDEN, refused.text
    assert pin._code(refused) == "MACHINE_CONSENT_REQUIRED", refused.text
    return refused.json()["details"]["app_id"]


async def test_an_app_is_refused_its_viewers_machine_until_they_say_yes(
    browser: AsyncClient, world
):
    as_app = pin._from_app(world["origin"], world["owner"])
    app_id = await _app_id(browser, world)

    # The page cannot say yes for its viewer: consents are a person's route.
    by_the_app = await browser.post(
        "/machine-consents", headers=as_app, json={"app_id": app_id}
    )
    assert by_the_app.status_code == status.HTTP_403_FORBIDDEN, by_the_app.text
    assert pin._code(by_the_app) == APP_ROUTE_NOT_ALLOWED, by_the_app.text

    granted = await browser.post(
        "/machine-consents",
        headers=pin._cookie(world["owner"]),
        json={"app_id": app_id},
    )
    assert granted.status_code == status.HTTP_201_CREATED, granted.text

    previews = await browser.get("/machines/me/previews", headers=as_app)
    assert previews.status_code == status.HTTP_200_OK, previews.text
    assert previews.json() == {"previews": []}


async def test_granted_or_not_the_browser_logins_and_reset_stay_the_persons(
    browser: AsyncClient, world
):
    as_app = pin._from_app(world["origin"], world["owner"])
    app_id = await _app_id(browser, world)
    await browser.post(
        "/machine-consents",
        headers=pin._cookie(world["owner"]),
        json={"app_id": app_id},
    )

    for method, path, body in (
        ("GET", "/machines/me/browser", None),
        ("POST", "/machines/me/browser:access", {"ttl_seconds": 60}),
        ("POST", "/machines/me/browser:navigate", {"url": "https://example.com"}),
        ("GET", "/machines/me/browser/logins", None),
        ("DELETE", "/machines/me/browser/logins/example.com", None),
        ("POST", "/machines/me:reset", {"scopes": ["home"], "confirm": True}),
        ("POST", "/machines/me:release", None),
    ):
        response = await browser.request(method, path, headers=as_app, json=body)
        assert response.status_code == status.HTTP_403_FORBIDDEN, (
            f"{method} {path}: {response.text}"
        )
        assert pin._code(response) == APP_ROUTE_NOT_ALLOWED, f"{method} {path}"


async def test_revoking_closes_the_machine_to_the_app_again(
    browser: AsyncClient, world
):
    as_app = pin._from_app(world["origin"], world["owner"])
    owner = pin._cookie(world["owner"])
    app_id = await _app_id(browser, world)
    await browser.post("/machine-consents", headers=owner, json={"app_id": app_id})
    assert (
        await browser.get("/machines/me/previews", headers=as_app)
    ).status_code == status.HTTP_200_OK

    revoked = await browser.delete(f"/machine-consents/{app_id}", headers=owner)
    assert revoked.status_code == status.HTTP_204_NO_CONTENT, revoked.text

    again = await browser.get("/machines/me/previews", headers=as_app)
    assert again.status_code == status.HTTP_403_FORBIDDEN, again.text
    assert pin._code(again) == "MACHINE_CONSENT_REQUIRED", again.text


async def test_one_viewers_yes_is_not_anothers(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    browser: AsyncClient,
    world,
):
    """Consent is per person: the editor saying yes opens nothing for the owner."""
    app_id = await _app_id(browser, world)
    granted = await browser.post(
        "/machine-consents",
        headers=pin._cookie(world["editor"]),
        json={"app_id": app_id},
    )
    assert granted.status_code == status.HTTP_201_CREATED, granted.text

    owners = await browser.get(
        "/machines/me/previews",
        headers=pin._from_app(world["origin"], world["owner"]),
    )
    assert pin._code(owners) == "MACHINE_CONSENT_REQUIRED", owners.text
