"""What a person does with their account, their organization, and each other.

Every method here is a product verb. None of them mention a path, a status code
or a payload shape — that is ``drivers/api.py``'s job. A scenario reads as a
sequence of things a person did, which is the point of the whole suite.
"""

from __future__ import annotations

import os
from typing import Any

from harness.run import a_name_for, must_be_traceable
from harness.drivers.api import items_of

JSON = dict[str, Any]

#: SuperTokens is mounted under /st and is not part of the documented OpenAPI
#: surface, so signing up has no operation id to reference. It is still the
#: first thing every person does, which is worth noticing: the single most
#: common action in the product is undocumented.
SIGNUP_PATH = "/st/auth/signup"

#: The other half of the capability the specification calls "Sign up and sign
#: in". Nothing proved it until the standing cast needed it: every person the
#: suite had ever made was brand new, so the product's most repeated action —
#: an existing person coming back — was the one action never exercised.
SIGNIN_PATH = "/st/auth/signin"

#: What the cast signs in with. A constant is right for a stack the suite boots
#: and throws away, and wrong the moment the cast holds real grants: once
#: somebody consents to GitHub, Slack or Gmail, this password and an address
#: anybody can derive from `tenant.py` are together enough to sign in as that
#: person and drive those accounts. On a deployment anyone can reach, that is
#: the whole of the protection.
#:
#: So it is settable, and defaults to the constant. A locally-booted stack is
#: unchanged — it is unreachable and holds nothing — and a deployment whose
#: cast has consented to anything sets this and stops publishing its own key.
PASSWORD_SETTING = "SCENARIOS_PASSWORD"
DEFAULT_PASSWORD = "ScenarioPassword@123"


def password() -> str:
    return os.getenv(PASSWORD_SETTING, "").strip() or DEFAULT_PASSWORD


def _already_registered(payload: JSON) -> bool:
    """Is this address simply already here?

    Two shapes mean the same thing, and only one of them is the name you would
    guess. SuperTokens has an `EMAIL_ALREADY_EXISTS_ERROR` status, but Lemma
    answers a taken address as a `FIELD_ERROR` against the email field — which
    is the friendlier thing to put in front of a person, and the reason
    provisioning has to read both.

    Deliberately narrow: a `FIELD_ERROR` about the *password* is a real
    failure, and treating it as "already here" would send provisioning off to
    sign in with a password the account does not have.
    """
    if payload.get("status") == "EMAIL_ALREADY_EXISTS_ERROR":
        return True
    if payload.get("status") != "FIELD_ERROR":
        return False
    return any(
        field.get("id") == "email" and "exist" in str(field.get("error", "")).lower()
        for field in payload.get("formFields", [])
        if isinstance(field, dict)
    )


class IdentitySteps:
    """Mixed into :class:`harness.world.Person`."""

    # --- account ---------------------------------------------------------

    async def signs_up(self, **kwargs: Any) -> JSON:
        """Register, which also signs the person in — there is no second step.

        ``**kwargs`` reaches the raw request untouched (``headers=``, most
        often) — this method has no opinion on what a deployment wants in
        front of signup, only on what signing up means once it is let
        through. A deployment that wants more than a password is a caller's
        problem to solve, not this suite's to know how.
        """
        return await self._enters(SIGNUP_PATH, doing="sign up", **kwargs)

    async def signs_in(self, **kwargs: Any) -> JSON:
        """Come back to an account that already exists.

        This is what a standing cast does at the start of every run, and it is
        why the cast can work against a deployment that a fresh signup could
        not: signing in passes none of the gates a real deployment keeps in
        front of registration.
        """
        return await self._enters(SIGNIN_PATH, doing="sign in", **kwargs)

    async def arrives(self, **kwargs: Any) -> bool:
        """Register if this address is new, sign in if it is not. New?

        What provisioning uses, and the order it tries them in is the point.
        Signing in first and reading the failure as "no account yet" would spend
        a real sign-in failure per person — and a deployment counts those: ten
        put a proof-of-work challenge in front of the next attempt and twenty
        block outright. Registering first costs one request either way and never
        fails on a person who is simply already here.
        """
        response = await self.api.call(
            "POST", SIGNUP_PATH, json=self._credentials(), **kwargs
        )
        payload = response.json() if response.content else {}
        if response.status_code == 200 and payload.get("status") == "OK":
            self._admitted(response, payload, doing="sign up")
            return True
        if _already_registered(payload):
            await self.signs_in()
            return False
        raise AssertionError(
            f"{self.label} could not take a seat on this deployment: "
            f"{response.status_code} {payload.get('status')!r}\n"
            f"  body: {response.text[:2000]}"
        )

    def _credentials(self) -> JSON:
        return {
            "formFields": [
                {"id": "email", "value": self.email},
                {"id": "password", "value": password()},
            ]
        }

    async def _enters(self, path: str, *, doing: str, **kwargs: Any) -> JSON:
        # Deliberately `call` rather than `expect`: the access token comes back
        # as a response *header*, so the decoded body alone is not enough.
        response = await self.api.call("POST", path, json=self._credentials(), **kwargs)
        if response.status_code != 200:
            raise AssertionError(
                f"{self.label} could not {doing}: {response.status_code}\n"
                f"  body: {response.text[:2000]}"
            )
        payload = response.json()
        if payload.get("status") != "OK":
            raise AssertionError(
                f"{self.label} could not {doing}: {payload.get('status')!r} — {payload}"
            )
        self._admitted(response, payload, doing=doing)
        return payload

    def _admitted(self, response: Any, payload: JSON, *, doing: str) -> None:
        token = response.headers.get("st-access-token") or response.cookies.get(
            "sAccessToken"
        )
        if not token:
            raise AssertionError(
                f"{self.label} completed {doing} but received no access token; "
                f"headers were {dict(response.headers)}"
            )
        self.api.authenticate(token)
        # From here on this person can let themselves back in, so a session
        # ageing out mid-run is the driver's problem rather than a scenario's.
        self.api.renews_with(self.signs_in)
        self.user_id = payload["user"]["id"]

    async def is_email_verified(self) -> bool:
        return bool((await self.api.get("/st/auth/user/email/verify")).get("isVerified"))

    async def requests_email_verification(self, **kwargs: Any) -> None:
        """Ask the deployment to send this person a verification email.

        ``**kwargs`` reaches the raw request, same as ``signs_up`` — a
        deployment that gates this the way it gates signup is a caller's
        problem to solve, not this method's to know how.
        """
        await self.api.post("/st/auth/user/email/verify/token", **kwargs)

    async def verifies_email(self, token: str) -> None:
        """Consume a verification token — the one a person gets by clicking
        the email's link, obtained however the caller obtained it."""
        payload = await self.api.post(
            "/st/auth/user/email/verify", json={"method": "token", "token": token}
        )
        if payload.get("status") != "OK":
            raise AssertionError(
                f"{self.label} could not verify their email: "
                f"{payload.get('status')!r} — {payload}"
            )

    async def profile(self) -> JSON:
        return await self.api.get("/users/me")

    async def sets_display_name(self, first: str, last: str = "") -> JSON:
        return await self.api.post(
            "/users/me/profile", json={"first_name": first, "last_name": last}
        )

    async def is_known_on_telegram_as(self, username: str) -> JSON:
        """Tell Lemma which Telegram account is this person.

        This is how a message from outside becomes a message from *somebody*:
        a sender whose `@username` matches a user's `telegram_username` resolves
        to that user, with no contact-share or linking round trip. Without it
        every inbound message is from a stranger, and a stranger is only ever
        told how to get access.
        """
        return await self.api.post(
            "/users/me/profile",
            what=f"{self.label} declaring their Telegram username",
            json={"telegram_username": username},
        )

    # --- organizations ---------------------------------------------------

    async def creates_an_organization(
        self, *, named: str | None = None, standing: bool = False
    ) -> JSON:
        name = named or a_name_for(f"{self.label}_org")
        if not standing:
            # Stricter than it looks, and deliberately: an organization cannot
            # be deleted through any API this product has, so one made under a
            # name nobody can trace is there for good.
            must_be_traceable(name, what="organization")
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

    async def home_of(self, organization: JSON) -> JSON:
        return await self.api.get(f"/organizations/{organization['id']}/home")

    async def opens_invitation(self, invitation: JSON) -> JSON:
        return await self.api.get(f"/organizations/invitations/{invitation['id']}")

    async def invitations_for(self, organization: JSON) -> list[JSON]:
        return items_of(
            await self.api.get(f"/organizations/{organization['id']}/invitations")
        )

    async def changes_role(self, person: Any, *, to: str, in_organization: JSON) -> JSON:
        member = await self.org_membership_of(person, in_organization=in_organization)
        return await self.api.patch(
            f"/organizations/{in_organization['id']}/members/{member['id']}/role",
            what=f"{self.label} making {person.label} a {to}",
            json={"role": to},
        )

    async def org_membership_of(self, person: Any, *, in_organization: JSON) -> JSON:
        """Someone's membership row in an organization.

        Named apart from `PodSteps.membership_of` deliberately: both mixins land
        on the same `Person`, so a shared name silently shadows one of them.
        `test_step_names_do_not_collide` in the suite guards against it.
        """
        for member in await self.members_of(in_organization):
            if str(member.get("user_id")) == str(person.user_id):
                return member
        raise AssertionError(
            f"{person.label} is not a member of {in_organization.get('name')!r}"
        )

    async def removes_from_organization(self, person: Any, *, organization: JSON) -> None:
        member = await self.org_membership_of(person, in_organization=organization)
        await self.api.delete(
            f"/organizations/{organization['id']}/members/{member['id']}",
            what=f"{self.label} removing {person.label} from the organization",
        )

    async def removes_membership(self, member: JSON, *, from_organization: JSON) -> None:
        """Remove a membership row, without needing the person behind it.

        The counterpart of `removes_from_organization`, which resolves a `Person`
        first. Cleanup has the row and not the person: whoever a run left in the
        organization is somebody it invented and has no handle on any more.
        """
        await self.api.delete(
            f"/organizations/{from_organization['id']}/members/{member['id']}",
            what=f"{self.label} removing a membership from "
            f"{from_organization.get('name')!r}",
        )

    async def whoami(self) -> JSON:
        """Resolve the current credential to who it says the caller is."""
        return await self.api.get("/auth/verify-token")
