# Slack native agent — live test plan

Companion to [slack-as-a-native-agent.md](slack-as-a-native-agent.md). Everything below is asserted only against fakes so far; this is the list of what a real workspace has to confirm.

---

## 0. Before anything — this will not work without a reinstall

**The manifest changed in ways that need a fresh OAuth install.** New bot scopes (`groups:read`, `groups:history`), a new event subscription (`message.groups`, `app_home_opened`, `member_joined_channel`), and a new feature block (`agent_view`). Slack does **not** grant new scopes to an existing install.

1. Open api.slack.com/apps → the Lemma app → **App Manifest**, paste [manifest.json](../../lemma-backend/manifests/slack/manifest.json), save.
2. Reinstall to the workspace, re-authorize.
3. Confirm the bot token's scopes include `assistant:write` and `groups:read`.

⚠️ **`agent_view` is one-way.** Once saved, the app cannot go back to `assistant_view`. Test on a dev app first if that matters.

**Watch during every test:** `docker compose logs -f` on the backend. Every failure path here logs at debug and returns quietly rather than raising — silence in Slack means a log line, not a crash.

---

## 1. Markdown rendering — the highest-value change

**Do:** DM the bot: *"Show me a table of three fruits with prices, a heading, and a bulleted list."*

| Expect | Why it matters |
|---|---|
| A real rendered table with cell borders | `TableBlock`/markdown tables — impossible before |
| A heading rendered as a heading | Previously printed `##` literally |
| Bullets, bold, and any code fence rendered | The whole `markdown` block promise |
| **No literal `**`, `##`, or `\|---\|` anywhere** | The specific bug this fixes |

**Also check:** the push notification on your phone shows a short one-line preview, **not** the whole answer body.

**Fails if:** you see raw Markdown syntax. That means the message went out as `text` instead of a `markdown` block.

---

## 2. Streaming + thinking steps — the biggest visual change

**Do:** ask something that forces tool use: *"Search the web for X and summarise it"* — anything multi-step.

| Expect |
|---|
| A message appears **immediately** and updates live |
| A collapsible timeline of steps, each completing as the next begins |
| The final answer lands **in that same message**, not a new one below |
| No leftover `⏳` message, and nothing gets deleted mid-flight |

**This is the riskiest change in the batch.** Specifically watch for:
- **Duplicate answers** (stream closed *and* a separate message posted) → `finish_progress` returned False but still delivered.
- **A stream that never closes** (spinner forever) → `chat.stopStream` failed.
- **Steps in the wrong order or duplicated** → the `step-N` chunk-id sequencing is wrong.

**Then force the fallback:** ask something with **no** tool calls (*"say hi"*). No progress stream should open, and the answer should arrive as a normal message.

---

## 3. Long answers

**Do:** *"Write me roughly 5,000 words about the history of the spreadsheet."*

Expect the answer split across **several messages**, each rendering as markdown, none truncated mid-sentence, all in the same thread. Confirm nothing is silently lost at the boundary.

---

## 4. Agent view, thread titles, working indicator

**Do:** open a **brand new** DM thread with the bot.

| Expect |
|---|
| Lemma appears in Slack's **Agents & AI Apps** section, not just Apps |
| The two manifest suggested prompts appear before you type |
| After your first message, the thread gets a **real title** (not "Lemma") |
| A working indicator appears while it thinks — status text or an `:eyes:` reaction |

**The indicator is the one that was actively broken.** Confirm it appears in **both** a DM and a channel mention. Previously a DM could show nothing at all.

**Second message in the same thread:** the title must **not** change. It is set once, at conversation creation.

---

## 5. Private channels

**Do:** invite the bot to a **private** channel, then `@Lemma hello`.

Expect a reply. Before this batch the event never arrived at all. Also confirm the channel picker in the Lemma web UI now lists private channels.

---

## 6. The invite flow (first step only)

**Do:** invite the bot to a **new public channel it has never been in**.

| Expect |
|---|
| **Only you** see an ephemeral message: *"I'm in #x. Nobody answers here yet…"* |
| It carries a **"Choose who answers"** button |
| Nobody else in the channel sees anything |

**Then click the button.** A modal opens listing **Pod assistant** first, then every agent in the pod. Save, and an ephemeral confirms what was set. Mention the bot in that channel and the chosen agent should answer.

**Also:** invite the bot to a channel that **already has a route**. No prompt should appear — that is a re-add, not setup.

---

## 7. Per-person DM agent

**Do:** open the app's **Home** tab → *Your direct messages* → **Change**. Pick an agent, save.

The tab republishes immediately with the new name, and your next DM should reach that agent. A colleague's DM still reaches the workspace default — the choice is per person.

**Then pick "Pod assistant."** It must *stay* on the pod assistant. Storing that as an empty value made it indistinguishable from "never chose", which silently resolved back to the default agent — a bug fixed three times in three places, so it is worth re-checking.

---

## 8. Your own Slack app (self-hosted / custom OAuth)

**Do:** `GET /pods/{pod_id}/surfaces/{name}/slack-manifest` returns a manifest with **this surface's** webhook URL and this deployment's redirect URL already filled in. Create a Slack app from it, then store that app's signing secret as the surface's `webhook_secret`.

Expect events from your own app to verify. A surface with no stored secret keeps using the deployment's app, unchanged. There is **no UI for this yet** — that is the next workstream.

---

## 9. Regression sweep — things that must still work

Nothing here is new, but the shared egress path changed underneath all of it.

- **Channel mention** → replies in thread
- **`ask_user`** → tappable options, answer resumes the run
- **`request_approval`** → Approve/Deny buttons work
- **File upload from the agent** → arrives as a real Slack file
- **File sent to the agent** → gets ingested
- **Telegram and Teams**, if you use them → progress still clears and answers still arrive. They share `progress_observer`, and their path was deliberately left on the old behavior.

---

## Verified live against a real workspace

Connector OAuth · `groups:read` + private channel listing · invite → inviter-only ephemeral → modal → route saved → confirmation · `agent_view` AGENT badge · suggested prompts · `assistant.threads.setStatus` and `setTitle` · markdown blocks rendering as prose · token streaming · App Home with agent/app cards · per-person DM agent picker.

Four bugs only a real workspace could have found: `streaming_mode_mismatch`, `expired_trigger_id`, reasoning leaking mid-stream, and `app_home.messages_tab_enabled` silently disabling DMs.

## Known gaps — do not test these, they are not built

| # | Gap |
|---|---|
| G1 | **The whole web UI is stale.** It still presents Slack as "pick a workspace, edit a routing table" — nothing mentions streaming, the App Home, per-person DM agents, or running your own Slack app. This is the next workstream. |
| G2 | No handler records feedback-button clicks; the buttons render only when a `feedback_callback_id` is passed, and nothing passes one. |
| G3 | Nothing prevents two surfaces claiming the same Slack workspace. Found live: routing was decided by row order across two orgs. |
| G4 | All of F: no link unfurling, no `assistant.search.context` (channel search is still the substring scan), no Slack DM cold-open. |
| G5 | The App Home logo needs `SLACK_HOME_LOGO_URL` set to a **public** https URL — Slack fetches it server-side, so localhost renders nothing. |
| G6 | Starter-prompt buttons show the prompt to send rather than posting as the user; Slack has no API to speak as a user. |

---

## Unit test baseline

`make test-backend-unit` → **3563 passed**. A different count means something regressed.
