"""What the deployment under test is configured to do.

[`credentials.py`](credentials.py) answers "is *this machine* configured for
GitHub?" by reading a local `.env`. That is the right question for a stack this
process boots and the wrong one for a deployment somewhere else: the file
describes a different machine. A suite that trusts it skips scenarios the
deployment could have run, and runs scenarios it cannot — both silently.

So the deployment is asked. `/health/capabilities` reports what it is configured
to do, and the capabilities here are shaped like `credentials.Capability` so
`needs(...)` works on them unchanged.

Two things this deliberately does not do. It never reads a secret — the document
carries booleans and enums about configuration, nothing else — and it never
guesses. A fact a deployment withholds is treated as absent, so a scenario
needing it skips with a reason rather than running on an assumption.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

CAPABILITIES_PATH = "/health/capabilities"

#: How long the suite waits for a target to describe itself. Short on purpose:
#: this runs before anything else, and a deployment that cannot answer a health
#: probe promptly is not one worth spending a suite on.
DESCRIBE_TIMEOUT = 30.0


class Unreachable(AssertionError):
    """The target could not be asked what it is."""


@dataclass(frozen=True, slots=True)
class Deployment:
    """How the target answered when asked what it is configured to do."""

    base_url: str
    environment: str
    llm_mode: str
    instance_id: str | None
    configuration: dict[str, Any]

    def says(self, fact: str) -> Any:
        """The target's answer, or ``None`` where it did not say.

        Withholding and denying are deliberately the same answer. A production
        deployment does not report its security posture, and a scenario that
        needs the posture relaxed must skip there — treating silence as consent
        is how a suite ends up asserting against a deployment that never agreed
        to any of it.
        """
        return self.configuration.get(fact)

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@dataclass(frozen=True, slots=True)
class Demand:
    """One configuration fact a capability needs, and the setting behind it."""

    fact: str
    wanted: Any
    setting: str

    def met_by(self, target: Deployment) -> bool:
        return target.says(self.fact) == self.wanted

    def as_instruction(self) -> str:
        wanted = (
            str(self.wanted).lower() if isinstance(self.wanted, bool) else self.wanted
        )
        return f"{self.setting}={wanted}"


@dataclass(frozen=True, slots=True)
class EnvironmentCapability:
    """Something the deployment must be configured for, asked of the deployment.

    The same four members `needs(...)` reads off a `credentials.Capability` —
    `name`, `missing`, `available`, `how` — so the two are interchangeable at a
    call site, and a scenario needing one of each reads as one sentence.
    """

    name: str
    demands: tuple[Demand, ...]
    how: str

    def missing_on(self, target: Deployment) -> tuple[str, ...]:
        """What that target would have to change. The whole rule lives here.

        Taking the target as an argument rather than reaching for the remembered
        one is what lets this be tested against a deployment that does not
        exist — which is the only way to check the answer for a production
        target without having one to hand.
        """
        return tuple(
            demand.as_instruction()
            for demand in self.demands
            if not demand.met_by(target)
        )

    @property
    def missing(self) -> tuple[str, ...]:
        return self.missing_on(described())

    @property
    def available(self) -> bool:
        return not self.missing


MODEL_IS_REAL = EnvironmentCapability(
    name="a model that actually thinks",
    demands=(Demand("llm_mode", "real", "E2E_LLM_MODE"),),
    how=(
        "the target is serving the deterministic scripted model, so an agent "
        "scenario written as a sentence a person would type proves nothing here"
    ),
)

OPEN_SIGNUP = EnvironmentCapability(
    name="signing a new person up",
    demands=(
        Demand("abuse_protection", False, "AUTH_ABUSE_PROTECTION_ENABLED"),
        Demand("email_verification_required", False, "AUTH_EMAIL_VERIFICATION_REQUIRED"),
        Demand(
            "email_deliverability_checks",
            False,
            "AUTH_EMAIL_DELIVERABILITY_CHECKS_ENABLED",
        ),
        Demand(
            "disposable_email_domains", False, "AUTH_DISPOSABLE_EMAIL_DOMAINS_ENABLED"
        ),
        Demand("altcha", False, "AUTH_ALTCHA_ENABLED"),
    ),
    how=(
        "the gates a real deployment keeps on. The standing cast signs *in*, so "
        "only the scenarios that are about signing up need these — and a "
        "production target withholds them, which is where these scenarios are "
        "meant to skip rather than run"
    ),
)

LOOPBACK_REACHABLE = EnvironmentCapability(
    name="a stand-in on this machine the deployment can call",
    demands=(
        Demand(
            "private_network_targets",
            True,
            "CONNECTOR_ALLOW_PRIVATE_NETWORK_TARGETS",
        ),
    ),
    how=(
        "the fakes in harness/fake_platform.py bind loopback, so only a "
        "deployment on this machine can reach them. Off in production on "
        "purpose: it is what stops an org admin pointing a connector at the "
        "cloud metadata service"
    ),
)


#: Set this to the target's own instance id and the suite refuses to run
#: anywhere else. Worth setting for any target you care about: it is what turns
#: a mistyped host from a silent write into a stopped run.
EXPECTED_INSTANCE = "SCENARIOS_TARGET_INSTANCE_ID"

#: Production is refused unless this says otherwise, in words.
ALLOW_PRODUCTION = "SCENARIOS_ALLOW_PRODUCTION"


def confirm_writable(target: Deployment) -> None:
    """Refuse a target this run has not been told it may write to.

    The suite creates real things and deletes most of them. Pointed at the wrong
    host it does that inside somebody's real workspace — and an organization it
    creates there is permanent, because the product has no way to delete one.
    There is no undo to fall back on, so the check goes in front.

    This is the counterpart of ``test_stack_never_inherits_real_infrastructure``,
    which stops a booted stack reaching a real deployment's storage. That guards
    a stack pointed at real data; this guards real data pointed at by a suite.
    """
    import os

    expected = os.getenv(EXPECTED_INSTANCE, "").strip()
    if expected and expected != (target.instance_id or ""):
        raise Unreachable(
            f"{target.base_url} says it is instance "
            f"{target.instance_id or '(unnamed)'!r}, and {EXPECTED_INSTANCE} "
            f"says this run may only write to {expected!r}. Refusing: the "
            f"organizations a run creates cannot be deleted afterwards."
        )

    if target.is_production and not _truthy(os.getenv(ALLOW_PRODUCTION)):
        raise Unreachable(
            f"{target.base_url} is a production deployment, and this suite "
            f"writes real data to whatever it is pointed at. Set "
            f"{ALLOW_PRODUCTION}=1 if that is genuinely what you meant, and "
            f"set {EXPECTED_INSTANCE} with it so a typo cannot reach a "
            f"different production."
        )


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes"}


BUNDLE_QUOTA = EnvironmentCapability(
    name="room to export and import bundles",
    demands=(
        Demand("bundle_daily_export_limit", 0, "POD_BUNDLE_DAILY_EXPORT_LIMIT"),
        Demand("bundle_daily_import_limit", 0, "POD_BUNDLE_DAILY_IMPORT_LIMIT"),
    ),
    how=(
        "five exports and five imports per user per UTC day, which is a fair "
        "guard on a real deployment and an impossible one for a standing cast: "
        "the packaging journey spends more than that in a single run, and the "
        "next run would start the day with none left. Zero disables the cap"
    ),
)


SERVER_SPEND_CAPS = EnvironmentCapability(
    name="a spend limit this run can actually reach",
    demands=(Demand("usage_limit_overrides", True, "USAGE_ORG_LIMIT_OVERRIDES_JSON"),),
    how=(
        "the limit under test has to be *set* before anything can exceed it, "
        "and a limit is deployment configuration rather than something a person "
        "can ask for. The stack caps one slug prefix at zero; a deployment "
        "that caps nothing has no refusal path to prove"
    ),
)


#: Not a fact about the deployment but about how this run was started, so it
#: is checked differently — see `available` below. Kept here anyway, because a
#: scenario asking "can I see what Lemma sent outward?" is asking the same kind
#: of question as "is a real model configured?", and one `needs(...)` should
#: answer both.
@dataclass(frozen=True, slots=True)
class EgressRecorded:
    name: str = "a record of what Lemma sent outward"
    how: str = (
        "run with SCENARIOS_EGRESS=record to drive the real providers, or "
        "=replay to serve what was recorded. Off by default, so the scenarios "
        "that never talk to a third party pay nothing for it"
    )

    @property
    def missing(self) -> tuple[str, ...]:
        from harness import egress

        return () if egress.wanted_mode() != "off" else ("SCENARIOS_EGRESS=replay",)

    @property
    def available(self) -> bool:
        return not self.missing


EGRESS_RECORDED = EgressRecorded()


_described: Deployment | None = None


def describe(base_url: str) -> Deployment:
    """Ask the target what it is, once, and remember the answer."""
    global _described
    try:
        response = httpx.get(
            f"{base_url.rstrip('/')}{CAPABILITIES_PATH}", timeout=DESCRIBE_TIMEOUT
        )
        response.raise_for_status()
        document = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise Unreachable(
            f"could not ask {base_url} what it is configured for "
            f"({CAPABILITIES_PATH}): {exc}. Every scenario decides from that "
            f"answer whether it can prove anything here, so the suite stops "
            f"rather than guessing."
        ) from exc

    configuration = document.get("configuration")
    if not isinstance(configuration, dict) or "llm_mode" not in configuration:
        raise Unreachable(
            f"{base_url} answered {CAPABILITIES_PATH} without saying how it is "
            f"configured. It is running a Lemma older than the one this suite "
            f"was written for; upgrade the target, or point the suite at a "
            f"stack it boots itself."
        )

    _described = Deployment(
        base_url=base_url.rstrip("/"),
        environment=str(configuration.get("environment", "unknown")),
        llm_mode=str(configuration["llm_mode"]),
        instance_id=document.get("instance_id"),
        configuration=configuration,
    )
    return _described


def described() -> Deployment:
    """The target's answer. Fails loudly rather than assuming anything."""
    if _described is None:
        raise Unreachable(
            "nothing has asked the target what it is configured for yet. The "
            "session fixture does that before any scenario runs, so a "
            "capability read outside a session is a harness bug."
        )
    return _described


def forget() -> None:
    """Drop the remembered answer. For the suite's own tests."""
    global _described
    _described = None
