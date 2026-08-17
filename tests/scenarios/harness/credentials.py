"""Credentials for the live lane, and the rules about them.

The fast lane stands in for third parties: Telegram's API and a provider that
publishes its own OpenAPI spec both run on localhost. That is right for a suite
on every push — it is fast, it is deterministic, and a Google outage does not
turn a pull request red.

It is also the one thing that lane cannot tell you: whether Lemma works against
the providers people actually connect. Token refresh, pagination, consent,
scopes, rate limits and the shapes real APIs return are exactly where an
integration breaks, and none of them exist on localhost.

So there is a second lane. Same scenarios' worth of rigour, real providers, and
a real model — run nightly and before a release rather than on every push.

Three rules hold it together:

* **Nothing is committed.** Credentials come from the environment, loaded from a
  gitignored `.env.live` locally and from repository secrets in CI. This file
  never prints a secret's value, including in a failure message.
* **Absent is skipped, never passed.** A missing credential skips the scenario
  with a reason naming exactly what is missing. A lane that quietly passes
  because it tested nothing is worse than no lane.
* **Live scenarios are opt-in.** They carry `@pytest.mark.live`, which the
  default run deselects. Nothing about the fast lane changes.

See `tests/scenarios/LIVE.md` for what to create and where to put it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pytest

#: Loaded once, from the suite root. Deliberately not `python-dotenv`: this is
#: twelve lines and the suite's dependency list is a promise about how little it
#: needs to talk to Lemma.
ENV_FILE = Path(__file__).resolve().parents[1] / ".env.live"


def _load_env_file() -> None:
    if not ENV_FILE.is_file():
        return
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        # Existing environment wins, so CI secrets are never shadowed by a file
        # somebody left behind on a runner.
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


_load_env_file()


@dataclass(frozen=True, slots=True)
class Provider:
    """A real third party the live lane can drive, and what it needs to do so."""

    name: str
    #: Environment variables that must all be present. Names only — values are
    #: never carried on this object, so it stays safe to put in a message.
    requires: tuple[str, ...]
    #: What a person has to do to produce them, for the skip message.
    how: str

    @property
    def missing(self) -> tuple[str, ...]:
        return tuple(name for name in self.requires if not os.environ.get(name))

    @property
    def available(self) -> bool:
        return not self.missing

    def value(self, name: str) -> str:
        secret = os.environ.get(name)
        if not secret:
            raise AssertionError(
                f"{name} is not set; this scenario should have been skipped"
            )
        return secret


GOOGLE = Provider(
    name="Google",
    requires=(
        "LIVE_GOOGLE_CLIENT_ID",
        "LIVE_GOOGLE_CLIENT_SECRET",
        "LIVE_GOOGLE_REFRESH_TOKEN",
    ),
    how=(
        "an OAuth client in Google Cloud console with the Calendar and Gmail "
        "scopes, authorised once by hand to produce a refresh token"
    ),
)

GITHUB = Provider(
    name="GitHub",
    requires=("LIVE_GITHUB_TOKEN", "LIVE_GITHUB_REPO"),
    how=(
        "a fine-grained personal access token scoped to one throwaway repo, "
        "with issues read/write; LIVE_GITHUB_REPO is 'owner/name'"
    ),
)

TELEGRAM = Provider(
    name="Telegram",
    requires=("LIVE_TELEGRAM_BOT_TOKEN", "LIVE_TELEGRAM_CHAT_ID"),
    how=(
        "a bot from @BotFather, and the chat id of a conversation with it "
        "(message the bot once, then read getUpdates)"
    ),
)

COMPOSIO = Provider(
    name="Composio",
    requires=("LIVE_COMPOSIO_API_KEY",),
    how="a Composio API key with the Gmail app connected to the same Google account",
)

MODEL = Provider(
    name="a real model",
    requires=("LIVE_MODEL_API_KEY",),
    how="an API key for the deployment's default model provider",
)

ALL = (GOOGLE, GITHUB, TELEGRAM, COMPOSIO, MODEL)


def needs(*providers: Provider) -> None:
    """Skip this scenario unless every provider it drives is configured.

    The reason names the missing variables and how to produce them, so a skip
    in a nightly report is actionable rather than a shrug. Called from the body
    rather than as a decorator, because a scenario often needs a provider *and*
    the model, and one sentence covering both reads better than two marks.
    """
    absent = [provider for provider in providers if not provider.available]
    if not absent:
        return
    lines = []
    for provider in absent:
        lines.append(
            f"{provider.name}: set {', '.join(provider.missing)} — {provider.how}"
        )
    pytest.skip("live credentials missing. " + "; ".join(lines))


def configured() -> tuple[str, ...]:
    """Which providers this run can actually drive, for the lane's own report."""
    return tuple(provider.name for provider in ALL if provider.available)
