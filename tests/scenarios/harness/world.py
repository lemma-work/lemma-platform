"""The world one scenario runs in.

A scenario never touches the stack directly. It asks the ``world`` for people,
and the people do things. That indirection buys two properties that matter more
than they look:

**Isolation by construction.** The stack is shared across the whole session
because booting it costs tens of seconds. Every person, organization and pod a
scenario creates therefore has to be unique to that scenario, and no scenario
may assert on a total ("there are 3 pods") because another scenario's pods are
in the same database. ``World.new_person`` makes the unique thing the default
thing, so the correct pattern is also the shortest one.

**One place to add a driver.** A ``Person`` holds a driver, not a URL. When the
CLI and SDK drivers land, the same journeys run through them by constructing
people with a different driver — the scenarios do not change at all.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from functools import partial
from typing import Any
from uuid import uuid4

import httpx

from harness import consent, tenant
from harness.drivers.api import ApiDriver, items_of
from harness.steps.agent import AgentSteps
from harness.steps.building import BuildingSteps
from harness.steps.datastore import DatastoreSteps
from harness.steps.identity import IdentitySteps
from harness.steps.pod import PodSteps
from harness.steps.surfaces import SurfaceSteps

JSON = dict[str, Any]

#: Sign-up validates the address before anything else, and rejects special-use
#: domains — `.test`, `.invalid`, `.localhost` all fail with "please use a valid
#: email address" however the deliverability checks are configured.
#: ``example.com`` is a real registered domain reserved for documentation, so it
#: passes syntax and, with deliverability off, never leaves the machine.
EMAIL_DOMAIN = "example.com"

#: How long any single request may take before the suite calls it a hang.
REQUEST_TIMEOUT = 150.0


@dataclass(eq=False)
class Person(
    IdentitySteps, PodSteps, DatastoreSteps, AgentSteps, BuildingSteps, SurfaceSteps
):
    """Someone using Lemma, and everything they can do.

    The verbs live in the ``steps`` mixins, one module per product noun, so this
    class stays a description of who a person *is* rather than a thousand-line
    grab bag.
    """

    label: str
    email: str
    api: ApiDriver
    user_id: str | None = None
    organization: JSON | None = None
    pod: JSON | None = None
    conversation: JSON | None = None

    def __repr__(self) -> str:
        return f"<Person {self.label} {self.email}>"


@dataclass
class Sessions:
    """What the standing cast is already holding, for the length of a run.

    Signing in is cheap but not free, and a deployment counts every call to its
    auth routes. Five sign-ins per run rather than five per scenario keeps the
    suite far under any gate a real deployment has — and makes the cast's
    sessions behave like a person's, opened once and used all day.

    Lives for the session; the `Person` objects that borrow from it do not.
    """

    tokens: dict[str, str] = field(default_factory=dict)
    user_ids: dict[str, str] = field(default_factory=dict)
    companies: dict[str, JSON] = field(default_factory=dict)

    #: How to build the tenant, when there is nothing to build it on yet. Set
    #: only for a stack this process booted — see the `sessions` fixture. Done
    #: on first use rather than at session start, so a run of scenarios that
    #: never asks for the cast pays nothing for it.
    build_tenant: Callable[[], Awaitable[Any]] | None = None
    _built: bool = False
    _connections_read: bool = False

    @property
    def connections_read(self) -> bool:
        return self._connections_read

    async def admit(self, person: Person) -> None:
        """Give this person their session, signing in only if there is none."""
        await self._tenant_exists()
        token = self.tokens.get(person.email)
        if token:
            person.api.authenticate(token)
            person.user_id = self.user_ids.get(person.email)
        else:
            await person.signs_in()
            self._remember(person)
        # Written after `signs_in`, which sets its own simpler renewal. This one
        # writes the fresh token back, so a session that ages out costs one
        # retry rather than one per scenario for the rest of the run.
        person.api.renews_with(partial(self._signs_in_again, person))

    async def company_of(self, person: Person, colleague: tenant.Colleague) -> JSON:
        """The organization they work for, as the product reports it."""
        known = self.companies.get(person.email)
        if known is not None:
            return known
        wanted = colleague.company.name
        mine = items_of(await person.api.get("/organizations"))
        for organization in mine:
            if organization.get("name") == wanted:
                self.companies[person.email] = organization
                return organization
        raise AssertionError(
            f"{person.label} does not belong to {wanted!r}; they are in "
            f"{[organization.get('name') for organization in mine]}. The tenant "
            f"has not been provisioned on this deployment, or somebody has "
            f"changed it — run `make scenarios-provision`."
        )

    async def note_who_is_connected(self, person: Person) -> None:
        """Which third parties somebody has actually connected here.

        Read from what the tenant *has*, not from what the deployment is
        configured for: a Google client id in an `.env` says Lemma could connect
        Gmail, never that anybody did.

        Once, lazily, and only for a run that uses the cast at all. A
        session-scoped fixture would have been the obvious home — this suite
        pins its event loop per function, so there cannot be one, and the lookup
        needs a signed-in person with a resolved organization anyway. It is
        called at the end of `World.person`, which is the first moment both are
        true.
        """
        if self._connections_read or not person.organization:
            return
        self._connections_read = True
        try:
            accounts = await person.accounts_in(person.organization)
        except Exception:  # noqa: BLE001 — an unprovisioned tenant says so elsewhere
            consent.remember(set())
            return
        consent.remember(
            {
                str(account.get("connector_id") or "")
                for account in accounts
                if account.get("connector_id")
            }
        )

    async def _tenant_exists(self) -> None:
        if self._built or self.build_tenant is None:
            return
        # Marked built only once it is. If provisioning fails, the next scenario
        # tries again and fails the same way — which is worth the repetition,
        # because the alternative is every later scenario reporting that nobody
        # belongs to Vantage Freight and none of them saying why.
        await self.build_tenant()
        self._built = True

    def _remember(self, person: Person) -> None:
        if person.api.token:
            self.tokens[person.email] = person.api.token
        if person.user_id:
            self.user_ids[person.email] = person.user_id

    async def _signs_in_again(self, person: Person) -> None:
        await person.signs_in()
        self._remember(person)
        person.api.renews_with(partial(self._signs_in_again, person))


@dataclass
class World:
    """Everything one scenario is allowed to reach."""

    base_url: str
    sessions: Sessions = field(default_factory=Sessions)
    _clients: list[httpx.AsyncClient] = field(default_factory=list)
    _people: dict[str, Person] = field(default_factory=dict)
    #: Re-entrancy guard: the lookup asks for a person, and that asks to look up.
    _reading_connections: bool = False

    async def new_person(self, label: str = "someone", *, sign_up: bool = True) -> Person:
        """A brand-new person, signed up and signed in.

        ``label`` is only for readable failures — "alice cannot see pod X" beats
        "user 4f2a… cannot see pod X". The email is always unique, whatever the
        label, so two scenarios both using "alice" never collide.
        """
        if label in self._people:
            raise AssertionError(
                f"this scenario already has a person called {label!r}; "
                f"give the second one a different label"
            )
        person = self.arriving(label, f"{label}-{uuid4().hex[:12]}@{EMAIL_DOMAIN}")
        if sign_up:
            await person.signs_up()
        self._people[label] = person
        return person

    async def person(self, label: str) -> Person:
        """Somebody who already works here, signed in and at their desk.

        The counterpart to `new_person`, and the one nearly every scenario
        wants. `new_person` invents a stranger; this is a colleague who was
        already there — which is what makes a scenario about a workspace with
        history in it possible at all, and what lets the suite run against a
        deployment where signing somebody up is gated.

        Asking twice in one scenario gives the same person back. They are the
        same human; making that an error would only push scenarios into
        threading a variable around to say so.
        """
        if label in self._people:
            return self._people[label]
        colleague = tenant.colleague(label)
        person = self.arriving(label, colleague.email)
        await self.sessions.admit(person)
        person.organization = await self.sessions.company_of(person, colleague)
        self._people[label] = person
        await self._note_who_is_connected()
        return person

    async def _note_who_is_connected(self) -> None:
        """Ask the one person whose accounts are the tenant's, once.

        An account is scoped to whoever connected it, so asking whichever
        colleague a scenario happened to want first answers about them — and a
        GitHub account connected by the holder reads as "nobody has connected
        GitHub" to every scenario that acts as somebody else. Which is exactly
        what happened: three GitHub scenarios skipped against a tenant that had
        GitHub connected all along.
        """
        if self.sessions.connections_read or self._reading_connections:
            return
        self._reading_connections = True
        try:
            holder = await self.person(tenant.CONNECTOR_HOLDER)
        finally:
            self._reading_connections = False
        await self.sessions.note_who_is_connected(holder)

    async def returning(self, person: Person, *, using: str | None = None) -> Person:
        """The same person, signing in again — a second device, a new day.

        `using` signs them in by a different spelling of the same address, which
        is how the specification's case-insensitivity clause gets checked: the
        person who signed up as `Ada@…` has to be the person who signs in as
        `ada@…`, and not a second account.
        """
        coming_back = self.arriving(person.label, using or person.email)
        await coming_back.signs_in()
        return coming_back

    def arriving(self, label: str, email: str) -> Person:
        """Somebody at the door, with no session yet.

        Public because provisioning needs it: it has to reach a person before
        anyone knows whether that person has an account. A scenario wants
        `person()` or `new_person()` instead — both of these leave the person
        signed in, which is the state a scenario is ever interested in.

        The timeout is generous because a client that gives up before the
        product does reports a hang the product never had. Provisioning a
        sandbox under load is the slow case, and it is slower than sixty
        seconds when several scenarios ask at once.
        """
        client = httpx.AsyncClient(base_url=self.base_url, timeout=REQUEST_TIMEOUT)
        self._clients.append(client)
        return Person(label=label, email=email, api=ApiDriver(client))

    async def aclose(self) -> None:
        for client in self._clients:
            await client.aclose()
        self._clients.clear()
        self._people.clear()
