# Slack: the frontend half

**Status:** Done, not yet verified against a live workspace · **Branch:** `slack-native-agent` · **PR:** [#303](https://github.com/lemma-work/lemma-platform/pull/303)

> **What this plan got wrong.** It said "the backend is done" for workstream 1. The domain layer was; the **API layer exposed almost none of it**. `use_pod_assistant` was missing from the route schemas *and* dropped by `_resolve_channel_routes`, so every save from the web UI collapsed the third state back to two — the branch's own bug, still live at the boundary, with the web UI as the trigger. `SurfaceConfigResponse` had no `slack` block, so `dm_agent_by_user` was unreadable. `webhook_secret` had no write path at all. And the manifest endpoint 500'd on every call: `MANIFEST_PATH` was off by one directory. None of that was reachable from the UI before, which is why none of it had been noticed.
>
> Two things it got right and worth repeating: the SDK chain is not optional, and nobody had watched these screens render.

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

## What shipped

### 0. The API boundary — *not in the original plan, and everything depended on it*

`use_pod_assistant` on `SurfaceChannelRouteInput`/`Response`, carried through `_resolve_channel_routes`; a `slack` block on `SurfaceConfigResponse` (`app_name`, `dm_agent_by_user`) with a write path for `app_name` only; `has_custom_slack_app` on the surface response; and `slack_signing_secret` as a write-only field on `SurfaceUpdateRequest`.

Two rules the schemas now enforce rather than imply: a route cannot claim both an agent and the pod assistant, and a settings save cannot touch `dm_agent_by_user` — that map is written from inside Slack, one person at a time, and one editor should not be able to reassign everybody.

### 1. Say what Slack now does

`DirectMessagesHeldChip` is gone, along with `surfaceDirectMessageAgent`. The premise underneath it was in the reach model itself: `surfaceReaches` treated DMs as belonging to exactly one agent. Now an agent reaches DMs when it is the surface default **or** anyone picked it, and the chip's tooltip says which.

`channelRouteAgent` resolves a route the way the backend does — explicit pod assistant, then named agent, then the surface default — so an unset route no longer renders as "the pod assistant answers here" when a named agent actually does.

The live step gained an `afterConnect` block: invite it to a channel, everyone picks their own DM agent, answers stream.

### 2. Channel-first

The picker reads `is_member` now. Every channel is still listed — the API returns them all — but non-members are marked, adding a route defaults to a channel the bot is actually in, and a route pointing somewhere it isn't warns that the route saves and nothing arrives. The false line is gone; the empty state teaches `/invite @Lemma`.

The route's agent select carries all three states: *Whoever answers DMs*, *Pod assistant*, or a named agent — matching Slack's own picker, and using the same `__pod_assistant__` value.

### 3. Your own Slack app

**One screen, in Connectors.** Slack's advanced setup shows a **Create your Slack app** button carrying the manifest as a `manifest_json` deep link — Slack opens with the name, scopes, event URL and OAuth callback already filled in — then takes the three values Slack will not hand over: client ID, client secret, signing secret.

Those three are the floor. Slack has no API that gives a third party another app's credentials; they exist only on the app's Basic Information page. Everything *above* that floor — knowing the callback URL, the scopes, the event URL — the manifest removes.

**The signing secret is org-level**, stored on the connector auth config beside the client id and secret, because all three belong to one Slack app. It used to sit on a surface, which forced the setup into two screens at two moments and made the ordering impossible: a surface is downstream of the app that creates it. Slack settings now carry a link to Connectors rather than a second input.

For every other connector the advanced dialog shows the OAuth redirect URL as a copyable field — it was showing nothing at all, so the first sign of a mismatch was the provider's own error page after the redirect.

**The webhook is shared, not per-surface.** The first version gave a custom app its own URL, because the shared endpoint verified with the deployment's secret before it knew which surface an event belonged to. That made the manifest depend on a surface id — backwards, since you need the app to get a client id, the client id to connect the account, and the account before a surface exists at all.

Now `/surfaces/webhooks/slack` reads `team_id` out of the (unverified) body, looks up the signing secrets of every surface bound to that workspace, and accepts a signature matching any of them — falling back to the deployment's secret only when no surface on that workspace runs its own app. Reading before verifying is safe here: the only thing read is the team id and the only thing it selects is which secret to try. A workspace that *does* run its own app never falls back, so holding the shared secret does not let you speak for it.

One workspace legitimately maps to several surfaces (see G3), and they hold copies of one app's secret — so "matches any candidate" is the same question as "came from that app". Which surface it belongs to is decided later, on verified content.

The manifest is therefore surface-independent and served platform-level at `GET /pods/{pod_id}/surface-setup/slack/manifest`.

**Scope note.** For a *self-hosted* deployment this already worked with no UI — set `SLACK_SIGNING_SECRET` / `SLACK_CLIENT_ID` / `SLACK_CLIENT_SECRET` to your own app's values. That is how this branch was tested. The UI matters for a shared deployment where each surface brings its own app.

---

## The copy was the other half of the work

Every string in the Slack and connectors flows was re-read against what the code now does. Most of what turned up was not jargon but **claims that had quietly stopped being true** when the backend moved underneath them:

- "Takes direct messages, plus any channel without a route of its own" — DMs are per person now, so the surface default answers only whoever hasn't chosen.
- "Who answers, and what gets through" — there is nothing to filter on Slack or Teams; that half described a mailbox.
- The Slack setup checklist listed four bot events where the manifest declares six, and marked a live surface as unfinished.
- The connect step still sent people to surface settings for a signing secret that had moved to Connectors.

The vocabulary now matches the in-Slack copy, which was already plain: **Pod assistant** everywhere (the web UI said "Pod default agent" in one place and "Pod assistant" in another for the same thing), *chose* rather than *picked*, and no "deployment", "auth config", "credential mode", "kind", or "route" in anything a person reads. Tests that pinned those words were updated to assert the intent instead — one now asserts the reason **does not** contain them.

The reach card also gained a caption. On Slack, Teams and email it has no QR or link to give the handle away, so it rendered as one bare word above a Copy button.

## Still to do

**Nobody has watched these screens render.** The dev stack runs and the live API carries every field, but the UI needs an authenticated session to reach, and none of it has been clicked. Sections 6, 7 and 8 of the [test plan](slack-native-agent-test-plan.md) are the ones that matter: the channel picker against a workspace where the bot is in some channels and not others, and the full bring-your-own-app round trip.

**`slack.app_name` has a write path and no UI.** It mirrors `telegram.app_name` — the pod app to feature in the App Home and channel bookmark bar — and the Telegram surface has a picker for it. Slack does not yet.

## The SDK chain

Not optional. The frontend never calls the backend directly:

1. From `lemma-backend`: dump a fresh spec — `uv run python -c "from app.app import app; import json; json.dump(app.openapi(), open('/tmp/openapi.fresh.json','w'))"`. `lemma-typescript/.generated/openapi.json` is gitignored and usually stale; do not trust it.
2. `cd lemma-typescript && OPENAPI_FILE=/tmp/openapi.fresh.json npm run generate:client`
3. **The surfaces namespace is hand-owned** — `generate:l2` only covers cleanly pod-scoped CRUD. Hand-edit `src/namespaces/pod-surfaces.ts`; `src/types.ts` re-exports the generated models wholesale, so new schemas need no export work.
4. `npm run build`, then frontend `npm run typecheck`.

The generator's `python` comes off `PATH` and this repo's `.python-version` pins a pyenv version that may not be installed — prefix with `PATH="$(pwd)/../lemma-backend/.venv/bin:$PATH"` if it fails on `pyenv: python: command not found`.

`lemma-frontend/node_modules/lemma-sdk` is a symlink to `../../lemma-typescript` and consumes its built `dist/`.

Note the **route inventory is generated from the committed OpenAPI spec**, not from live routes — so the architecture ratchet will pass even when a new endpoint is missing from it. That is how the new manifest route went unnoticed at first.

---

## Backend surface the UI can rely on

| Endpoint / field | Purpose |
|---|---|
| `GET .../surfaces/{name}/slack-manifest` | Manifest with this surface's URLs filled in |
| `GET .../surfaces/{name}/channels` | Channels, each with `is_member` — **use the flag** |
| `surface.config.slack.dm_agent_by_user` | Per-person DM agent; `__pod_assistant__` is an explicit value, absence means "never chose" |
| `surface.config.channels[].use_pod_assistant` | Explicit third state, distinct from an unset `agent_name` |
| `surface.config.slack.app_name` | Pod app to feature (mirrors `telegram.app_name`) — writable, no UI yet |
| `GET /surface-setup/slack/manifest` | No pod, no org — it describes the deployment. Signed-in access is the only gate, and every value in it is already public |
| `auth_configs.config.signing_secret` | The org's own Slack app signing secret, beside its client id and secret |

**Three states, not two** is the recurring trap. "Named agent", "pod assistant", and "not configured" are different, and collapsing the last two was the same bug three times in this branch.

---

## Gotchas that cost real time

- **Slack caches the App Home** until a new view is published. A failed publish leaves the *previous* view on screen, which looks exactly like a deploy that did not happen.
- **`trigger_id` dies in ~3 seconds.** Modal opening runs in the HTTP request, not the worker — a queue is slower than that.
- **A `view_submission` response body is protocol.** Slack parses it as a `response_action`; anything unexpected surfaces as *"We had some trouble connecting."* Return an empty 200.
- **Slack fetches images server-side**, so a `localhost` logo URL renders an empty box.
- **`agent_view` is one-way.** Once a real app manifest takes it, there is no going back to `assistant_view`.
- **A file-path constant is not covered by unit tests that never load the file.** `MANIFEST_PATH` was off by one directory for the life of the branch; every manifest request 500'd, and nothing caught it because nothing called it. The same shape of bug hid an `ImportError` in the new controller import — 585 unit tests passed, and the app would not boot. Start the stack.
