# The live lane

The scenario suite has two lanes, and they answer different questions.

| | Fast lane | Live lane |
|---|---|---|
| **Question** | Does Lemma work? | Does Lemma work *against the real providers people connect*? |
| **Runs** | every push, ~6 minutes | nightly, and before every release |
| **Third parties** | stood in for on localhost | real Google, GitHub, Telegram, Composio |
| **Model** | deterministic | the real one |
| **A red run means** | Lemma broke | Lemma broke, **or** a provider did |
| **Selected by** | default | `-m live` |

Lemma itself is real in both. Postgres, Redis, SuperTokens, the API, the worker
and the function sandboxes all run for real on every push — what the fast lane
stands in for is the far end of an integration, never Lemma.

The live lane exists because that far end is where integrations actually break:
token refresh, consent, scopes, pagination, rate limits, and the shapes real
APIs return. None of those exist on localhost.

---

## Running it

```bash
make scenarios-live
```

Providers you have not configured are **skipped, with a reason naming what is
missing**. A skip is not a pass, and the report says so — a lane that goes green
because it tested nothing is worse than no lane at all.

To run one provider's scenarios:

```bash
cd tests/scenarios && uv run pytest -m live journeys/live/test_github.py
```

---

## Credentials

Put them in `tests/scenarios/.env.live`, which is gitignored. **Never commit
them, and never paste them into an issue, a pull request, or a chat** — this is
a public repository.

```
# copy to tests/scenarios/.env.live and fill in
LIVE_MODEL_API_KEY=

LIVE_GITHUB_TOKEN=
LIVE_GITHUB_REPO=your-name/lemma-live-scenarios

LIVE_GOOGLE_CLIENT_ID=
LIVE_GOOGLE_CLIENT_SECRET=
LIVE_GOOGLE_REFRESH_TOKEN=

LIVE_TELEGRAM_BOT_TOKEN=
LIVE_TELEGRAM_CHAT_ID=

LIVE_COMPOSIO_API_KEY=
```

In CI the same names are repository secrets. They are available to the nightly
workflow and to a manual run, and **never to a pull request** — this repository
is public, and a fork must not be able to read them.

### What to create

**GitHub** — the cheapest to set up, and the one to start with. A fine-grained
personal access token scoped to a single throwaway repository, with read/write
on issues. No OAuth round trip. Scenarios create issues and close them; point it
at a repository you do not mind being written to.

**Google** — an OAuth client in the Google Cloud console with the Calendar and
Gmail scopes. Authorise it once by hand; keep the **refresh token**. Consent
needs a browser, so this step cannot be automated, and it is the only manual
step. Calendar is reached through Lemma's native connector; Gmail through
Composio on the same account, which is also what proves the two paths agree
about who the account belongs to.

**Telegram** — a bot from `@BotFather`. Message it once and read `getUpdates` to
find the chat id. The runner needs no public URL: the worker can receive by
polling (`enable_telegram_polling_mode`), which the live lane turns on.

**Composio** — an API key with the Gmail app connected to the same Google
account as above.

### What the credentials can reach

Use throwaway resources. A dedicated Google account, a repository created for
this, a bot that is in no real group. Scenarios create and delete real things —
calendar events, issues, messages — and a suite pointed at anything you care
about will eventually delete something you wanted.

---

## Reporting

The nightly workflow posts to Slack: how many scenarios passed, which providers
were skipped and why, and a link to the run. A release build posts the same
summary, so "did the integrations still work" is answered before a release goes
out rather than after.

Set `SLACK_WEBHOOK_URL` as a repository secret. Without it the workflow still
runs and still fails on a real failure; it just says nothing to Slack.

---

## Writing a live scenario

```python
@scenario("A person connects Calendar and the agent reads their week")
@proves("PS-CONN-020")
@covers("connector.account.create", "connector.operation.execute")
@pytest.mark.live
async def test_calendar_is_readable(world):
    needs(GOOGLE, MODEL)
    ...
```

`needs(...)` skips with a reason naming the missing variables. `@pytest.mark.live`
keeps it out of the fast lane.

The rest of the suite's rules still apply, and matter more here: no mocking, no
sleeping, product language in the steps, and every scenario declaring what it
proves. A live scenario that leaves rubbish behind in a real account is a bug in
the scenario — clean up in a fixture's teardown, and make the cleanup
unconditional.
