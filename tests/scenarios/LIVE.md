# The live lane

Two lanes, answering different questions.

| | Fast lane | Live lane |
|---|---|---|
| **Question** | Does Lemma work? | Does Lemma work *against the real providers people connect*? |
| **Runs** | every push, ~6 minutes | nightly, and before every release |
| **Third parties** | stood in for on localhost | real Google, GitHub, Telegram, Composio |
| **Model** | deterministic | the one the deployment is configured with |
| **A red run means** | Lemma broke | Lemma broke, **or** a provider did |
| **Selected by** | default | `-m live` |

Lemma itself is real in both. Postgres, Redis, SuperTokens, the API, the worker
and the function sandboxes all run for real on every push — what the fast lane
stands in for is the far end of an integration, never Lemma.

## There are no test-only credentials

The lane is configured the way a **deployment** is configured. It reads
`lemma-backend/.env` — the same file `make dev` uses, and the same variable
names `app/core/config.py` and the connector module read:

| Setting | Lights up |
|---|---|
| `LEMMA_OPENAI_API_KEY`, `LEMMA_OPENAI_DEFAULT_MODEL` | agents on a real model |
| `CONNECTOR_GITHUB_CLIENT_ID` / `_SECRET` | the GitHub connect flow |
| `CONNECTOR_GOOGLE_CLIENT_ID` / `_SECRET` | the Google connect flow |
| `COMPOSIO_API_KEY` | Composio's toolkits in the catalogue |
| `TELEGRAM_BOT_TOKEN` | a real Telegram surface |
| `SLACK_BOT_TOKEN` | a real Slack surface |

Configure the server and the lane lights up. That is the whole mechanism —
there is no parallel namespace to keep in step.

Running from a git worktree works: `.env` is gitignored, so a worktree has none,
and the lane falls back to the main checkout's. `LEMMA_ENV_FILE` overrides both.

**The stack never inherits where your data lives.** The deployment's settings go
*underneath* the stack's own, and every setting that decides where state lives —
both database URLs, Redis, SuperTokens, both storage roots, the mail transport —
is set explicitly by the stack and therefore wins.
`test_stack_never_inherits_real_infrastructure` fails the build if that ordering
is ever broken. Without it a scenario run would create and delete records in
your real dev database.

## Which real resources a run may write to

Distinct from configuration: these say *what this run is allowed to touch*, and
they have no business in a server's config.

| Setting | Meaning |
|---|---|
| `SCENARIOS_GITHUB_REPO` | `owner/name` of a throwaway repository |
| `SCENARIOS_GITHUB_TOKEN` | a fine-grained PAT for it, with issues read/write |
| `SCENARIOS_TELEGRAM_CHAT_ID` | a chat with the bot; message it once, then read `getUpdates` |

Use throwaway resources. Scenarios create and delete real things — issues,
messages, calendar events — and a lane pointed at something you care about will
eventually delete something you wanted.

## Running it

```bash
make scenarios-live
```

Anything the deployment is not configured for is **skipped, with a reason naming
the setting**. A skip is not a pass, and the Slack summary reports skips as
prominently as failures — a lane that goes green because it tested nothing is
worse than no lane.

It is slower than the fast lane, deliberately: importing Composio's full
catalogue is minutes on its own, so the per-test bound is 15 rather than 3.

## Consent, and what cannot be automated

Connecting Google means consenting in a browser, and Google **deliberately
blocks automated sign-in**. A scenario driving the consent screen would be
fighting a defence Google maintains on purpose, and would be the flakiest thing
in the suite. So the lane does not pretend, and splits in two:

**What a fresh stack can prove** — the connect flow the deployment hands a
person: the right client id, the scopes an operation will need, `access_type=
offline` so a refresh token is issued at all. A rotated client, a dropped scope
or a stale redirect is silent until somebody tries to connect, and this is what
catches them.

**What needs an account somebody already connected** — actually executing
Calendar or Gmail operations. Consent once, by hand, through the real interface
on a persistent environment, and point the lane at it:

```bash
cd tests/scenarios && uv run pytest -m live --base-url https://your-lemma
```

GitHub sidesteps the problem: a fine-grained PAT is a bearer token and is a real
way to connect GitHub, so those scenarios run on a fresh stack with no browser
involved.

**Being a person on Telegram** is the third shape of this, and the sharpest. A
bot never receives a message nobody sent it, and cannot send one *as* a human —
so the half of a messaging surface that matters most could not be driven at all
until the suite had a real account to be. That needs MTProto, which needs a
session, and Telegram mints a session by sending a code to a phone.

So it is a person's job, once:

```bash
cd tests/scenarios && uv run python -m harness.telegram_login
```

Put the `TELEGRAM_SESSION` it prints in the environment and the person-driven
scenarios (`journeys/live/test_telegram_person.py`) run unattended from then on
— text in and an answer out, an image the agent has to look at, a question
answered by pressing a button, an approval offered as something to press.

Two things that account needs, and both are what a real colleague has anyway:

* **a @username** — Lemma resolves a Telegram sender by it, and without one every
  message is from a stranger. The scenarios skip and say so.
* **to have messaged the bot once** — a bot cannot open a conversation, which is
  why the surface it talks to stands between runs (`tenant.STANDING_REACH`).

The session is that account without a password prompt. Keep it out of the
repository and out of anywhere shared.

**Run the live lane with the proxy off.** The fast lane has the egress proxy
answer for `api.telegram.org` so nothing leaves the machine, and that is exactly
wrong here: the agent would reply to the fake while the person waits on real
Telegram — two conversations that never meet, and a wait that times out saying
nothing useful.

```bash
SCENARIOS_EGRESS=off uv run pytest -m live --base-url http://127.0.0.1:8000
```

The scenarios refuse to run against a faked Telegram rather than time out, so
this is a message and not a mystery.

## Reporting

The nightly workflow posts to Slack: how many scenarios passed, what was not run
and why, and a link. Releases post the same summary, so "did the integrations
still work" is answered before a release rather than after.

Set `SLACK_WEBHOOK_URL` as a repository secret. Without it the workflow still
runs and still fails on a real failure; it just says nothing to Slack.

In CI the same variable names are repository secrets. They are available to the
nightly and manual runs and **never to a pull request** — this repository is
public, and a fork must not be able to read them.

## Writing a live scenario

```python
@scenario("A person connects GitHub and Lemma knows whose account it is")
@proves("PS-CONN-011")
@covers("connector.account.create", "connector.operation.execute")
async def test_connecting_github_identifies_the_account(github):
    needs(GITHUB_REPO, REAL_MODEL)
    ...
```

`needs(...)` skips with a reason naming the missing settings.
`pytest.mark.live` on the module keeps it out of the fast lane.

The rest of the suite's rules still apply, and matter more here: no mocking, no
sleeping, product language in the steps, every scenario declaring what it
proves. A live scenario that leaves rubbish behind in a real account is a bug in
the scenario — clean up in a `finally`, and make the cleanup unconditional.
