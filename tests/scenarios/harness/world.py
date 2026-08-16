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

JSON = dict[str, Any]

#: Sign-up validates the address before anything else, and rejects special-use
#: domains — `.test`, `.invalid`, `.localhost` all fail with "please use a valid
#: email address" however the deliverability checks are configured.
#: ``example.com`` is a real registered domain reserved for documentation, so it
#: passes syntax and, with deliverability off, never leaves the machine.
EMAIL_DOMAIN = "example.com"


@dataclass(eq=False)
class Person(IdentitySteps, PodSteps, DatastoreSteps, AgentSteps, BuildingSteps):
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
        client = httpx.AsyncClient(base_url=self.base_url, timeout=60.0)
        self._clients.append(client)
        person = Person(
            label=label,
            email=f"{label}-{uuid4().hex[:12]}@{EMAIL_DOMAIN}",
            api=ApiDriver(client),
        )
        if sign_up:
            await person.signs_up()
        self._people[label] = person
        return person

    async def aclose(self) -> None:
        for client in self._clients:
            await client.aclose()
        self._clients.clear()
        self._people.clear()
