"""What this deployment is configured to reach, read from the backend's own env.

The fast lane stands in for the far end of an integration: Telegram's API and a
provider that publishes its own OpenAPI spec both run on localhost, and the
model is deterministic. That is right for a suite on every push, and it is also
the one thing that lane cannot tell you — token refresh, consent, scopes,
pagination and the shapes real APIs return do not exist on localhost, and they
are where integrations actually break.

So there is a second lane, and it is configured the way a deployment is
configured. **There are no test-only credential variables.** A live scenario
asks whether the server has `COMPOSIO_API_KEY`, `CONNECTOR_GITHUB_CLIENT_ID`,
`LEMMA_OPENAI_API_KEY` — the same names `app/core/config.py` and the connector
module read. Configure the server and the lane lights up; that is the whole
mechanism.

Three rules:

* **Nothing is committed, and nothing is printed.** Values come from
  `lemma-backend/.env` (or `LEMMA_ENV_FILE`) and from the process environment.
  This module never puts a secret in a message, including a failure.
* **Absent is skipped, never passed.** A missing setting skips with a reason
  naming exactly which variable the server wants. A lane that goes green
  because it tested nothing is worse than no lane.
* **Live scenarios are opt-in.** They carry `@pytest.mark.live`, which the
  default run deselects.

See `tests/scenarios/LIVE.md`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[3] / "lemma-backend"


def _main_checkout_env() -> Path | None:
    """The `.env` in the main checkout, when this is a git worktree.

    `.env` is gitignored, so a worktree starts without one — and a worktree is
    how most of this work happens. Without this, running the live lane from a
    branch means either copying secrets around or wondering why every scenario
    skips on a machine that is fully configured.
    """
    import subprocess

    try:
        common = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=str(BACKEND_ROOT.parent),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if common.returncode != 0:
        return None
    candidate = Path(common.stdout.strip()).parent / "lemma-backend" / ".env"
    return candidate if candidate.is_file() else None


def _env_file() -> Path:
    """The deployment's own configuration file — the one `make dev` uses.

    `LEMMA_ENV_FILE` points the lane at a different configuration without
    copying anything around.
    """
    override = os.getenv("LEMMA_ENV_FILE")
    if override:
        return Path(override)
    here = BACKEND_ROOT / ".env"
    return here if here.is_file() else (_main_checkout_env() or here)


ENV_FILE = _env_file()


def load_deployment_env() -> dict[str, str]:
    """Read the backend's `.env` into a plain mapping.

    Deliberately not `python-dotenv`: this is a dozen lines, and the suite's
    dependency list is a promise about how little it needs in order to talk to
    Lemma over HTTP.

    Returns the file's contents rather than mutating `os.environ`, because the
    stack has to layer these *under* its own infrastructure settings — see
    `stack._environment`. Loading them into this process would put a developer's
    real `DATABASE_URL` one careless line away from a disposable stack.
    """
    if not ENV_FILE.is_file():
        return {}
    values: dict[str, str] = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def _configured() -> dict[str, str]:
    """The deployment's settings: the file, with the environment winning."""
    return {**load_deployment_env(), **os.environ}


@dataclass(frozen=True, slots=True)
class Capability:
    """Something a configured deployment can reach, and what configures it."""

    name: str
    #: Settings that must all be present. Names only — a value never lands on
    #: this object, so it stays safe to put in a skip message.
    requires: tuple[str, ...]
    #: What an operator does to provide them.
    how: str

    @property
    def missing(self) -> tuple[str, ...]:
        settings = _configured()
        return tuple(name for name in self.requires if not settings.get(name))

    @property
    def available(self) -> bool:
        return not self.missing

    def value(self, name: str) -> str:
        secret = _configured().get(name)
        if not secret:
            raise AssertionError(
                f"{name} is not configured; this scenario should have skipped"
            )
        return secret


REAL_MODEL = Capability(
    name="a real model",
    requires=("LEMMA_OPENAI_API_KEY", "LEMMA_OPENAI_DEFAULT_MODEL"),
    how="the model settings any deployment needs to run an agent at all",
)

GITHUB = Capability(
    name="GitHub",
    requires=("CONNECTOR_GITHUB_CLIENT_ID", "CONNECTOR_GITHUB_CLIENT_SECRET"),
    how="the GitHub OAuth app a deployment registers so its users can connect",
)

GOOGLE = Capability(
    name="Google",
    requires=("CONNECTOR_GOOGLE_CLIENT_ID", "CONNECTOR_GOOGLE_CLIENT_SECRET"),
    how=(
        "the Google OAuth client for connectors — separate from the login "
        "client, because connecting Calendar or Gmail needs app-specific scopes"
    ),
)

COMPOSIO = Capability(
    name="Composio",
    requires=("COMPOSIO_API_KEY",),
    how="the Composio key that brings its toolkits into the connector catalogue",
)

TELEGRAM = Capability(
    name="Telegram",
    requires=("TELEGRAM_BOT_TOKEN",),
    how="a bot token from @BotFather, which is what a surface authenticates as",
)

SLACK = Capability(
    name="Slack",
    requires=("SLACK_BOT_TOKEN",),
    how="the Slack app's bot token",
)

#: A person's *connected account* is not deployment configuration — it is the
#: result of them consenting in a browser. A scenario that needs one names the
#: chat, repository or calendar it may use, because those are choices about
#: which real resources this run is allowed to write to.
TELEGRAM_CHAT = Capability(
    name="a Telegram chat to talk in",
    requires=("SCENARIOS_TELEGRAM_CHAT_ID",),
    how=(
        "the chat id of a conversation with the bot: message it once, then read "
        "getUpdates. Points the lane at a chat you do not mind being written to"
    ),
)

GITHUB_REPO = Capability(
    name="a GitHub repository to write to",
    requires=("SCENARIOS_GITHUB_REPO", "SCENARIOS_GITHUB_TOKEN"),
    how=(
        "'owner/name' of a throwaway repository, and a fine-grained PAT for it. "
        "A PAT is a real way to connect GitHub and needs no browser consent — "
        "the OAuth app above is what a person would use instead"
    ),
)

ALL = (REAL_MODEL, GITHUB, GOOGLE, COMPOSIO, TELEGRAM, SLACK)


def needs(*capabilities: Capability) -> None:
    """Skip unless the deployment is configured for everything this drives.

    The reason names the settings and what they are for, so a skip in a nightly
    report is actionable rather than a shrug. Called from the body rather than
    as a decorator, because a scenario usually needs a provider *and* a real
    model, and one sentence covering both reads better than two marks.
    """
    absent = [capability for capability in capabilities if not capability.available]
    if not absent:
        return
    pytest.skip(
        "this deployment is not configured for it. "
        + "; ".join(
            f"{capability.name}: set {', '.join(capability.missing)} — {capability.how}"
            for capability in absent
        )
    )


def configured() -> tuple[str, ...]:
    """What this run can actually reach, for the lane's own report."""
    return tuple(capability.name for capability in ALL if capability.available)
