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

import os
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import httpx

from harness.drivers.api import ApiDriver
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

#: A real deployment rate-limits signup at 5 per 15 minutes per IP (see
#: lemma-backend's abuse_middleware.py) — abuse protection working exactly as
#: intended against a CI runner that otherwise signs up a fresh account for
#: nearly every one of ~380 scenarios from one IP. A local stack has no such
#: limit and every scenario runs against a throwaway database anyway, so this
#: is off by default and only matters when pointed at a real, persistent
#: deployment: set SCENARIOS_ACCOUNT_POOL_SIZE to reuse that many
#: already-provisioned accounts (round-robin per scenario) instead of signing
#: up fresh every time. Safe to turn on broadly: the suite's own isolation
#: rule — assert only on what you created, never a total — never depended on
#: the *account* being new, only on the *resources* under it being new, and
#: that is unchanged. The few scenarios that genuinely prove something about
#: signing up itself opt out with ``pool=False``.
_POOL_SIZE = int(os.environ.get("SCENARIOS_ACCOUNT_POOL_SIZE", "0"))


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
class World:
    """Everything one scenario is allowed to reach."""

    base_url: str
    _clients: list[httpx.AsyncClient] = field(default_factory=list)
    _people: dict[str, Person] = field(default_factory=dict)

    async def new_person(
        self, label: str = "someone", *, sign_up: bool = True, pool: bool = True
    ) -> Person:
        """A brand-new person, signed up and signed in.

        ``label`` is only for readable failures — "alice cannot see pod X" beats
        "user 4f2a… cannot see pod X". The email is always unique, whatever the
        label, so two scenarios both using "alice" never collide — unless
        account-pool mode is on (see ``_POOL_SIZE``), in which case the
        *account* is one of a small reused set and only the resources this
        scenario creates under it are unique. Pass ``pool=False`` to opt a
        scenario out and force a real signup regardless — required for the
        handful of scenarios that prove something about signing up itself,
        never needed for anything else.
        """
        if label in self._people:
            raise AssertionError(
                f"this scenario already has a person called {label!r}; "
                f"give the second one a different label"
            )
        # Generous, because a client timeout that fires before the product
        # gives up reports a hang the product never had. Provisioning a sandbox
        # under load is the slow case, and it is slower than sixty seconds when
        # several scenarios ask at once.
        client = httpx.AsyncClient(base_url=self.base_url, timeout=REQUEST_TIMEOUT)
        self._clients.append(client)
        if sign_up and pool and _POOL_SIZE:
            person = await self._pooled_person(label, client)
        else:
            person = Person(
                label=label,
                email=f"{label}-{uuid4().hex[:12]}@{EMAIL_DOMAIN}",
                api=ApiDriver(client),
            )
            if sign_up:
                await person.signs_up()
        self._people[label] = person
        return person

    async def _pooled_person(self, label: str, client: httpx.AsyncClient) -> Person:
        """Sign in as one of the reused pool accounts, provisioning it once.

        Slots are handed out round-robin by call order within this scenario
        (``World`` is function-scoped, so this always starts at 0) — the pool
        only has to be at least as large as the most people any one scenario
        creates, which today is 3. Signing in never touches the tight signup
        bucket at all (only recorded *failures* count against a signin, per
        abuse_middleware.py), so once every slot exists this costs nothing.
        The first time a slot is asked for on a given deployment, signing in
        fails because the account doesn't exist yet — that failure is the
        provisioning signal, so this signs it up once and every run after
        this signs straight in.
        """
        slot = len(self._people) % _POOL_SIZE
        person = Person(
            label=label,
            email=f"scenario-pool-{slot}@{EMAIL_DOMAIN}",
            api=ApiDriver(client),
        )
        try:
            await person.signs_in()
        except AssertionError:
            await person.signs_up()
        return person

    async def aclose(self) -> None:
        for client in self._clients:
            await client.aclose()
        self._clients.clear()
        self._people.clear()
