"""Things only a person can do, said clearly enough that they get done.

Gmail, GitHub and Slack are OAuth2 connectors, and the product deliberately has
no way to store one without a browser — `connector_service.py` refuses
credential injection outright. That is the right call and we are not adding a
back door for tests: consenting in a browser *is* what a real person does, and a
suite whose purpose is to use the product as a person does should be using the
same door.

So a person consents once, on the standing tenant, and these describe what they
have to do.

The reason this is a module rather than a line in a README is what happens
afterwards. Grants expire, tokens get revoked, somebody rebuilds the dev
deployment. The failure mode of "the scenario skips" is a suite that quietly
proves less every month and never says so — which is the same silent rot the
loopback fakes had, arriving by a different route. So an unmet consent is
carried to the end of the run and reported as its own thing:

    waiting on a person
      Gmail, connected to Vantage Freight
        why   4 scenarios could not run
        do    sign in as priya.raman@…, open Connectors, choose Gmail, allow
        then  make scenarios-provision TARGET=… to confirm

and `scripts/report_scenarios_to_slack.py` says the same in Slack, so an expired
grant becomes somebody's morning rather than a number that quietly went down.
"""

from __future__ import annotations

from dataclasses import dataclass


#: How a consent skip announces itself, all the way to Slack. See `sentence`.
WAITING = "waiting on a person:"


@dataclass(frozen=True, slots=True)
class HumanAction:
    """One prerequisite a person has to satisfy, and how.

    Shaped like `credentials.Capability` — `name`, `missing`, `available`,
    `how` — so `needs(...)` takes it unchanged and a scenario needing a real
    model *and* a connected mailbox still reads as one sentence.
    """

    name: str
    #: The connector whose account has to exist. Matched against what the tenant
    #: actually has, not against configuration — a client id in an `.env` says
    #: the deployment *could* connect Gmail, never that anybody did.
    connector: str
    company: str
    how: str

    @property
    def missing(self) -> tuple[str, ...]:
        _asked_for.add(self)
        if _connected is None:
            raise NotLookedUpYet(
                f"nothing has asked the tenant which accounts it has, so "
                f"{self.name!r} cannot be checked. The session fixture does "
                f"that before any scenario runs."
            )
        if self.connector in _connected:
            return ()
        return (f"a connected {self.connector} account in {self.company}",)

    @property
    def available(self) -> bool:
        return not self.missing

    @property
    def sentence(self) -> str:
        """Read by `needs()`. Says who has to act, not which setting is unset.

        The lead is fixed on purpose: a skip reaches CI as a string in a JUnit
        file and nothing else, so `report_scenarios_to_slack.py` keys on
        `WAITING` to tell "somebody has to click something" apart from "not
        applicable here". Change it in both places or in neither.
        """
        return f"{WAITING} nobody has connected {self.name} — {self.how}"


class NotLookedUpYet(AssertionError):
    """Asked whether a person had done something, before anybody looked."""


GMAIL = HumanAction(
    name="Gmail for Vantage Freight",
    connector="gmail",
    company="Vantage Freight",
    how=(
        "sign in as the cast's owner, open Connectors, choose Gmail and allow "
        "it. Use the mailbox the suite is allowed to write to — scenarios send "
        "real mail and delete it again"
    ),
)

GITHUB = HumanAction(
    name="GitHub for Vantage Freight",
    connector="github",
    company="Vantage Freight",
    how=(
        "open Connectors, choose GitHub and authorise it against the throwaway "
        "repository named by SCENARIOS_GITHUB_REPO. Scenarios open and close "
        "real issues there"
    ),
)

SLACK = HumanAction(
    name="Slack for Vantage Freight",
    connector="slack",
    company="Vantage Freight",
    how=(
        "open Connectors, choose Slack, and install the app into the throwaway "
        "workspace. Scenarios post and read real messages in it"
    ),
)

EVERY = (GMAIL, GITHUB, SLACK)


#: What the tenant actually has, looked up once a session. `None` until then, so
#: asking early fails loudly rather than reporting everything as missing —
#: which would read exactly like nobody having consented to anything.
_connected: frozenset[str] | None = None

#: Which actions scenarios have actually asked for. Only these are reported: a
#: run of the data journeys should not end by asking somebody to connect Slack.
_asked_for: set[HumanAction] = set()


def remember(connectors: set[str]) -> None:
    """Record which connectors the tenant has an account for."""
    global _connected
    _connected = frozenset(connectors)


def forget() -> None:
    """For the suite's own tests."""
    global _connected
    _connected = None
    _asked_for.clear()


def outstanding() -> list[HumanAction]:
    """What a person still has to do, among the things this run wanted."""
    if _connected is None:
        return []
    return sorted(
        (action for action in _asked_for if action.connector not in _connected),
        key=lambda action: action.name,
    )
