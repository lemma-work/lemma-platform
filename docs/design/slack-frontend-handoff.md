# Slack: the frontend half

**Status:** Not started · **Branch:** `slack-native-agent` · **PR:** [#303](https://github.com/lemma-work/lemma-platform/pull/303)

The backend for Slack was rewritten ([design doc](slack-as-a-native-agent.md), [test plan](slack-native-agent-test-plan.md)) and verified against a live workspace. The web UI was not touched. It now describes an integration that no longer exists.

This is the plan for the other half. Read the design doc first for *what* Slack does now; this covers only what the UI has to become.

---

## The one-line problem

Everything new happens **inside Slack** — invite the bot and it asks who should answer; open the App Home and pick your own DM agent. The web UI still presents Slack as *"pick a workspace, then edit a routing table"*, which was true until this branch.

---

## What the UI does not know

| Shipped | UI says |
|---|---|
| Answers stream token by token | nothing |
| Markdown, tables, headings render natively | nothing |
| An App Home tab with agents, apps, starter prompts | nothing |
| Inviting the bot to a channel *is* setup | "add a channel here" |
| Each person picks the agent answering their own DMs | "one agent answers DMs" (the `DirectMessagesHeldChip` premise) |
| A workspace can run its own Slack app | no path at all |

---

## Three workstreams, in the order I would do them

### 3. Say what Slack now does — *cheapest, highest value*

Mostly copy. The surfaces page, the reach chips, and the connect flow should mention streaming, the App Home, and per-person DM agents. Someone reading it today has no idea any of it exists.

**Delete `DirectMessagesHeldChip`** ([agent-surfaces-row.tsx:238](../../lemma-frontend/components/surfaces/agent-surfaces-row.tsx:238)). It exists to name the single agent holding a workspace's DMs and grey out every other agent. That constraint is gone — `dm_agent_by_user` is per person — so the chip now asserts something false.

### 2. Channel-first

The sharper version of "channel-first" is that **the picker should stop being the primary path**. An invite is setup now. The web UI's job becomes showing what is already true and letting you change it, not being where you construct it.

**One concrete lie to fix.** [surface-configure-step.tsx:149](../../lemma-frontend/components/surfaces/surface-configure-step.tsx:149) says *"Only channels the bot has been invited to appear here."* `list_channels` returns **every** public channel with an `is_member` flag, which the frontend fetches ([surface-configure-step.tsx:25](../../lemma-frontend/components/surfaces/surface-configure-step.tsx:25)) and never reads. So the picker offers channels that will silently never deliver, while claiming the opposite. One root cause, two visible bugs.

The empty state should teach the invite flow instead of dead-ending.

### 1. Your own Slack app — *needs the SDK regenerated first*

**The backend is done.** `GET /pods/{pod_id}/surfaces/{surface_name}/slack-manifest` returns the committed manifest with that surface's webhook URL and this deployment's redirect URL substituted. A surface holding its own signing secret verifies against it; one without keeps using the deployment's app.

The UI needs: a "Use your own Slack app" panel — the manifest in a copy box, the three steps, and a field for the app's signing secret (stored as the surface's `webhook_secret`). `setup_guides.py` already contains most of this copy, gated on `is_custom_app`, written and never rendered.

**Scope note.** For a *self-hosted* deployment this already works with no UI at all — set `SLACK_SIGNING_SECRET` / `SLACK_CLIENT_ID` / `SLACK_CLIENT_SECRET` to your own app's values. That is how this branch was tested. The UI matters for a shared deployment where each surface brings its own app.

---

## Before writing any code

**Open the app and walk the Slack flow.** `make dev` (or `make dev-public` for a tunnel), frontend on `:3710`. Everything above was inferred from reading; nobody has watched these screens render since the backend changed. Expect the plan to shift.

**The SDK chain is not optional** for workstream 1. The frontend never calls the backend directly:

1. From `lemma-backend`: dump a fresh spec — `uv run python -c "from app.app import app; import json; json.dump(app.openapi(), open('/tmp/openapi.fresh.json','w'))"`. The committed `lemma-typescript/.generated/openapi.json` is usually stale; do not trust it.
2. `cd lemma-typescript && OPENAPI_FILE=/tmp/openapi.fresh.json npm run generate:client`
3. **The surfaces namespace is hand-owned** — `generate:l2` only covers cleanly pod-scoped CRUD. Hand-edit `src/namespaces/surfaces.ts`, wire into `src/client.ts`, export from `src/index.ts`.
4. `npm run build`, then frontend `npm run typecheck`.

`lemma-frontend/node_modules/lemma-sdk` is a symlink to `../../lemma-typescript` and consumes its built `dist/`.

Note the **route inventory is generated from the committed OpenAPI spec**, not from live routes — so `make architecture` will pass even when a new endpoint is missing from it. That is how the new manifest route went unnoticed at first.

---

## Backend surface the UI can rely on

| Endpoint / field | Purpose |
|---|---|
| `GET .../surfaces/{name}/slack-manifest` | Manifest with this surface's URLs filled in |
| `GET .../surfaces/{name}/channels` | Channels, each with `is_member` — **use the flag** |
| `surface.config.slack.dm_agent_by_user` | Per-person DM agent; `__pod_assistant__` is an explicit value, absence means "never chose" |
| `surface.config.channels[].use_pod_assistant` | Explicit third state, distinct from an unset `agent_name` |
| `surface.config.slack.app_name` | Pod app to feature (mirrors `telegram.app_name`) |
| `surface.webhook_secret` | The org's own Slack signing secret, when running their own app |

**Three states, not two** is the recurring trap. "Named agent", "pod assistant", and "not configured" are different, and collapsing the last two was the same bug three times in this branch.

---

## Gotchas that cost real time

- **Slack caches the App Home** until a new view is published. A failed publish leaves the *previous* view on screen, which looks exactly like a deploy that did not happen.
- **`trigger_id` dies in ~3 seconds.** Modal opening runs in the HTTP request, not the worker — a queue is slower than that.
- **A `view_submission` response body is protocol.** Slack parses it as a `response_action`; anything unexpected surfaces as *"We had some trouble connecting."* Return an empty 200.
- **Slack fetches images server-side**, so a `localhost` logo URL renders an empty box.
- **`agent_view` is one-way.** Once a real app manifest takes it, there is no going back to `assistant_view`.
