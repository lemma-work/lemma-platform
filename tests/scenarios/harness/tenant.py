"""The people the suite runs as, and what they are to each other.

A scenario suite that makes a new person, a new organization and a new pod for
every test never once exercises the situation every real user is in: a workspace
with history in it, colleagues who were already there, and an account that has
been used before. It also cannot be pointed at a real deployment, because an
organization cannot be deleted — every run would leave more of them behind,
permanently.

So the suite has a **standing tenant** instead. Vantage Freight is a freight
company that uses Lemma; Calder Retail is one of its customers, and exists so
that "somebody outside is refused" has a genuine outsider to be refused. The
cast are colleagues with settled roles, and they sign **in** rather than up —
which is what lets the same suite run against a deployment whose registration
gates are on, because signing in passes none of them.

Five people is not an arbitrary number. It is the smallest cast that can express
every permission promise the specification makes: proving the last owner may not
leave needs somebody who is genuinely the last owner, and proving an outsider is
refused needs somebody who genuinely works somewhere else.

Nothing here is machinery. This module is the *declaration* — provisioning reads
it to build the tenant, and teardown reads it to put the tenant back — so the
two can never drift into disagreeing about what the tenant is supposed to be.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

#: The cast's email domain. Configurable because a deployment that checks
#: deliverability wants a domain that resolves, and `example.com` does not have
#: an MX record. The default is what the local stack accepts.
DOMAIN_SETTING = "SCENARIOS_TENANT_DOMAIN"
DEFAULT_DOMAIN = "example.com"


def domain() -> str:
    return os.getenv(DOMAIN_SETTING, "").strip() or DEFAULT_DOMAIN


@dataclass(frozen=True, slots=True)
class Company:
    """An organization that stands between runs."""

    key: str
    name: str

    #: Always INVITE_ONLY, and this is not a detail. `email_domain` is UNIQUE
    #: across a deployment, and an organization under the EMAIL_DOMAIN join
    #: policy claims its domain permanently — there is no way to delete an
    #: organization and no way to release the claim without one. A tenant that
    #: took that policy could be provisioned exactly once, ever, per deployment.
    join_policy: str = "INVITE_ONLY"


VANTAGE = Company(key="vantage", name="Vantage Freight")
CALDER = Company(key="calder", name="Calder Retail")

COMPANIES = (VANTAGE, CALDER)


@dataclass(frozen=True, slots=True)
class Colleague:
    """Somebody who works here, and what they may do."""

    #: What scenarios call them. Short, because it reads in failures:
    #: "daniel cannot see the sales pod" beats "user 4f2a… cannot see it".
    label: str
    full_name: str
    mailbox: str
    company: Company
    role: str
    #: Why the cast has this person at all. A role nobody can justify is a role
    #: somebody will quietly repurpose, and then two scenarios disagree about
    #: who Sofia is.
    exists_for: str

    @property
    def email(self) -> str:
        return f"{self.mailbox}@{domain()}"


CAST = (
    Colleague(
        label="priya",
        full_name="Priya Raman",
        mailbox="priya.raman",
        company=VANTAGE,
        role="ORG_OWNER",
        exists_for="the owner path — the buck stops here, and she cannot leave",
    ),
    Colleague(
        label="daniel",
        full_name="Daniel Okonkwo",
        mailbox="daniel.okonkwo",
        company=VANTAGE,
        role="ORG_EDITOR",
        exists_for="the administering path — runs the pods without owning the company",
    ),
    Colleague(
        label="sofia",
        full_name="Sofia Marchetti",
        mailbox="sofia.marchetti",
        company=VANTAGE,
        role="ORG_MEMBER",
        exists_for="the ordinary teammate, who most scenarios are actually about",
    ),
    Colleague(
        label="wei",
        full_name="Wei Chen",
        mailbox="wei.chen",
        company=VANTAGE,
        role="ORG_MEMBER",
        exists_for=(
            "the churn role: joined, promoted, demoted, removed and joined "
            "again. Scenarios that change somebody's standing change Wei's, so "
            "that the rest of the cast stays where the next run expects it"
        ),
    ),
    Colleague(
        label="hannah",
        full_name="Hannah Weber",
        mailbox="hannah.weber",
        company=CALDER,
        role="ORG_OWNER",
        exists_for=(
            "the outsider every refusal scenario needs. She is refused because "
            "she genuinely works somewhere else, not because a flag says so"
        ),
    ),
)

BY_LABEL = {colleague.label: colleague for colleague in CAST}

#: The person scenarios may change the standing of. See `wei` above.
CHURN = "wei"


@dataclass(frozen=True, slots=True)
class StandingPod:
    """A pod that is already there when a run starts, with history in it."""

    name: str
    holds: str


#: Named the way that team would name them, not the way a test suite would.
#: A run opens these; it does not make them. Which is also what a person does —
#: they do not create a new pod for every task.
STANDING_PODS = (
    StandingPod("sales", "quotes and rate tables — tables, records, files"),
    StandingPod("operations", "dispatch — schedules, triggers, workflows, functions"),
    StandingPod("customer-support", "agents, conversations, surfaces"),
    StandingPod("internal-tools", "what the ops team builds for itself — bundles, apps"),
    StandingPod("company-wide", "sharing, roles, reach"),
)


@dataclass(frozen=True, slots=True)
class StandingConnector:
    """A connector the tenant keeps one auth config for, forever.

    The durable thing is the auth config, because an account is bound to the
    one it was consented against. Install a fresh auth config each run and
    every OAuth connector needs a person in a browser again each run — which
    makes the consent design unusable for exactly the connectors that need it.

    So this is named without a run mark, deliberately. `must_be_traceable`
    covers pods and organizations and not this, so nothing has to be relaxed:
    a name with no mark is invisible to cleanup, which is the point.
    """

    connector: str
    #: Which of the connector's kinds to install. `None` when it offers one.
    #: `gmail` needs "composio": its own package kind is OAuth2 against a
    #: Google client we do not have, while Composio brings its own OAuth app —
    #: and that is the route this product's users take today.
    kind: str | None = None
    #: Whether a person has to open a browser. A bot token does not need one.
    consented: bool = True


#: One per provider the suite drives for real. `whatsapp` and the rest of the
#: catalogue are deliberately absent: this list is what provisioning installs
#: and asks somebody to connect, and asking for accounts no scenario uses is
#: how a consent report becomes noise somebody learns to ignore.
STANDING_CONNECTORS = (
    StandingConnector("telegram", consented=False),
    StandingConnector("github"),
    StandingConnector("slack"),
    StandingConnector("gmail", kind="composio"),
)


@dataclass(frozen=True, slots=True)
class StandingReach:
    """A surface that stands between runs, because what it can reach does.

    A bot cannot cold-DM. Outbound goes through a conversation the person
    started, and that link is keyed by *surface* — so a surface created fresh
    each run can never send to anybody, and the live scenario that tried it
    waited two minutes for a reply to a message Lemma had refused to send.

    Standing, one message from one person makes the surface reachable for every
    run afterwards. The same trade as the standing pods, for the same reason.
    """

    pod: str
    platform: str
    connector: str
    agent: str
    name: str


STANDING_REACH = (
    StandingReach(
        pod="customer-support",
        platform="TELEGRAM",
        connector="telegram",
        agent="frontdesk",
        name="telegram",
    ),
)


#: Whose accounts they are. An account is scoped to the person who connected
#: it — the list endpoint filters on `user_id` — so "is GitHub connected?" has
#: no answer for the organization, only for a person in it. One nominated
#: holder is what keeps that from being accidental: without it, provisioning
#: asks the owner, a scenario asks whoever it happens to be acting as, and the
#: same tenant answers yes and no to the same question.
#:
#: Daniel rather than the owner: he administers every standing pod, so the
#: surfaces that bind these accounts are already his to manage, and an editor
#: installing a connector is the ordinary case rather than the privileged one.
CONNECTOR_HOLDER = "daniel"


def standing_auth_config_name(connector: str) -> str:
    """What the tenant's own auth config for a connector is called."""
    return f"{connector}_standing"


def colleague(label: str) -> Colleague:
    """The cast member by that name, or a failure naming who there is."""
    try:
        return BY_LABEL[label]
    except KeyError:
        raise AssertionError(
            f"nobody in the cast is called {label!r}. The tenant has "
            f"{', '.join(sorted(BY_LABEL))} — add them to harness/tenant.py and "
            f"re-provision, or use world.new_person() for somebody who is "
            f"genuinely a stranger."
        ) from None
