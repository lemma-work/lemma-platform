"""What a person does with their account, their organization, and each other.

Every method here is a product verb. None of them mention a path, a status code
or a payload shape — that is ``drivers/api.py``'s job. A scenario reads as a
sequence of things a person did, which is the point of the whole suite.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from harness.drivers.api import items_of

JSON = dict[str, Any]

#: SuperTokens is mounted under /st and is not part of the documented OpenAPI
#: surface, so signing up has no operation id to reference. It is still the
#: first thing every person does, which is worth noticing: the single most
#: common action in the product is undocumented.
SIGNUP_PATH = "/st/auth/signup"

PASSWORD = "ScenarioPassword@123"


class IdentitySteps:
    """Mixed into :class:`harness.world.Person`."""

    # --- account ---------------------------------------------------------

    async def signs_up(self) -> JSON:
        # Deliberately `call` rather than `expect`: the access token comes back
        # as a response *header*, so the decoded body alone is not enough. A
        # person who has just registered is signed in — there is no second step.
        response = await self.api.call(
            "POST",
            SIGNUP_PATH,
            json={
                "formFields": [
                    {"id": "email", "value": self.email},
                    {"id": "password", "value": PASSWORD},
                ]
            },
        )
        if response.status_code != 200:
            raise AssertionError(
                f"{self.label} could not sign up: {response.status_code}\n"
                f"  body: {response.text[:2000]}"
            )
        payload = response.json()
        if payload.get("status") != "OK":
            raise AssertionError(
                f"{self.label} could not sign up: {payload.get('status')!r} — {payload}"
            )
        token = response.headers.get("st-access-token") or response.cookies.get(
            "sAccessToken"
        )
        if not token:
            raise AssertionError(
                f"{self.label} signed up but received no access token; "
                f"headers were {dict(response.headers)}"
            )
        self.api.authenticate(token)
        self.user_id = payload["user"]["id"]
        return payload

    async def profile(self) -> JSON:
        return await self.api.get("/users/me")

    async def sets_display_name(self, first: str, last: str = "") -> JSON:
        return await self.api.post(
            "/users/me/profile", json={"first_name": first, "last_name": last}
        )

    # --- organizations ---------------------------------------------------

    async def creates_an_organization(self, *, named: str | None = None) -> JSON:
        name = named or f"{self.label.title()} Org {uuid4().hex[:8]}"
        organization = await self.api.post(
            "/organizations",
            what=f"{self.label} creating organization {name!r}",
            json={"name": name},
        )
        self.organization = organization
        return organization

    async def renames_organization(self, organization: JSON, *, to: str) -> JSON:
        return await self.api.patch(
            f"/organizations/{organization['id']}",
            what=f"{self.label} renaming {organization.get('name')!r} to {to!r}",
            json={"name": to},
        )

    async def is_refused_renaming(self, organization: JSON, *, to: str) -> int:
        response = await self.api.call(
            "PATCH", f"/organizations/{organization['id']}", json={"name": to}
        )
        if response.status_code < 400:
            raise AssertionError(
                f"{self.label} was expected to be refused renaming "
                f"{organization.get('name')!r}, but it succeeded "
                f"({response.status_code})"
            )
        return response.status_code

    async def organizations(self) -> list[JSON]:
        return items_of(await self.api.get("/organizations"))

    async def belongs_to_no_organization(self) -> None:
        found = await self.organizations()
        if found:
            raise AssertionError(
                f"{self.label} was expected to belong to no organization, "
                f"but belongs to {[o.get('name') for o in found]}"
            )

    async def navigation(self) -> JSON:
        return await self.api.get("/organizations/navigation")

    async def members_of(self, organization: JSON) -> list[JSON]:
        return items_of(
            await self.api.get(f"/organizations/{organization['id']}/members")
        )

    async def own_membership_of(self, organization: JSON) -> JSON:
        for member in await self.members_of(organization):
            if str(member.get("user_id")) == str(self.user_id):
                return member
        raise AssertionError(
            f"{self.label} is not a member of organization {organization.get('name')!r}"
        )

    async def own_role_in(self, organization: JSON) -> str:
        return str((await self.own_membership_of(organization))["role"])

    # --- invitations -----------------------------------------------------

    async def invites(
        self,
        person: "Any",
        *,
        to: JSON,
        as_role: str = "ORG_MEMBER",
        pod: JSON | None = None,
        pod_role: str | None = None,
    ) -> JSON:
        body: JSON = {"email": person.email, "role": as_role}
        if pod is not None:
            body["pod_id"] = str(pod["id"])
            if pod_role:
                body["pod_role"] = pod_role
        return await self.api.post(
            f"/organizations/{to['id']}/invitations",
            what=f"{self.label} inviting {person.label} to {to.get('name')!r}",
            json=body,
        )

    async def invitations(self) -> list[JSON]:
        return items_of(await self.api.get("/organizations/invitations"))

    async def accepts(self, invitation: JSON) -> JSON:
        return await self.api.post(
            f"/organizations/invitations/{invitation['id']}/accept",
            what=f"{self.label} accepting an invitation",
        )

    async def is_refused_invitation(self, invitation: JSON) -> int:
        response = await self.api.call(
            "POST", f"/organizations/invitations/{invitation['id']}/accept"
        )
        if response.status_code < 400:
            raise AssertionError(
                f"{self.label} was expected to be refused this invitation, "
                f"but it was accepted ({response.status_code})"
            )
        return response.status_code

    async def revokes(self, invitation: JSON) -> None:
        await self.api.delete(
            f"/organizations/invitations/{invitation['id']}",
            what=f"{self.label} revoking an invitation",
        )
