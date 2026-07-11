# Agent interaction, voice & approval tools

These are the tools an agent calls **at runtime to interact with the user** — ask a
question, show something, request approval for a privileged action, speak, or listen.
They are distinct from the builder-facing Toolsets table in `agents.md`: that table says
*which* toolsets to enable on an agent; this doc says *how those tools behave* and how a
UI handles their round-trip.

Two toolsets gate them (grant them in the agent's `toolsets`, see `agents.md`):

| Toolset | Tools |
| --- | --- |
| `USER_INTERACTION` | `ask_user`, `display_resource`, `request_approval` |
| `SPEECH` | `say`, `listen` |

Every tool returns at least `{ success, message?, error? }` (errors are non-fatal — a
failed tool returns `success:false` with `error`, it does not crash the run); the
tool-specific fields are listed per tool below.

> The pod-default assistant has both toolsets. A user-created agent gets a tool only if
> its `toolsets` include the gating toolset — and for grant-checked actions, only on
> resources it has been granted (`agents.md` → Workload grants).

---

## How they reach the user (the surface matrix)

The same tool call renders differently depending on where the conversation runs. The
agent does not branch on this — it calls the tool the same way everywhere; the backend
delivers it per surface.

| Tool | Web app / custom app | Chat surface (Slack/Teams) | Chat surface (Telegram/WhatsApp) | Email (Gmail/Outlook) |
| --- | --- | --- | --- | --- |
| `ask_user` | card rendered from the tool call | **native tappable options** | options as a formatted message; user types their pick | (not used — agent asks in prose in the reply) |
| `display_resource` (FILE) | rendered from the tool result | native file attachment / download link | native file / link | **not delivered** — attach via the email reply tool |
| `display_resource` (WIDGET) | embedded iframe | link to the served widget | link to the served widget | not delivered |
| `display_resource` (TABLE/AGENT/…) | inline resource view | delivered as a link/summary | link/summary | not delivered |
| `say` | audio player | **native voice note** (MP3) | **native voice note** (OGG voice bubble) | not delivered |
| `request_approval` | approval card | approval card | approval card | (asks in prose) |

Ground truth: `agent_surfaces/platforms/platform_capabilities.py` (per-platform
capabilities) and `agent/tools/user_interaction/pydantic_adapter.py`
(`_maybe_deliver_to_surface`). **On email surfaces, `display_resource` does not reach the
recipient** — share files through the reply tool's `attachment_paths`.

## The pause / resume model

`ask_user` and `request_approval` are **pausing** tools. When the agent calls one, the
in-process run ends cleanly and the **conversation flips to `WAITING`** (the pending tool
call is persisted). When the user answers/decides, the backend synthesizes the tool's
return value and starts a **fresh run** that resumes from history — the agent sees the
answer as that tool's result and continues.

Daemon harnesses (Codex / Claude Code / OpenCode) own their own session and **cannot
pause mid tool-call**; there, `ask_user` returns `success:false` with a message telling
the agent to ask the question in prose and end its turn instead. Build agents so a
prose-question fallback still works.

`display_resource`, `say`, and `listen` do **not** pause — they return immediately.

---

## `ask_user`

Ask one or more multiple-choice questions and wait for the answers. Use it for a choice
among known options. For free-form or multi-field input, ask clearly in prose and end
the turn so the user's next message can answer.

**Request**

```jsonc
{
  "questions": [
    {
      "header": "Environment",            // short label; also the answer key
      "question": "Which environment should I deploy to?",
      "options": [
        { "label": "Staging", "description": "safe, resets nightly", "recommended": true },
        { "label": "Production", "description": "live traffic" }
      ],
      "multi_select": false               // optional, default false
    }
  ]
}
```

- 2–4 `options` per question. The client **always** adds an "Other" free-form choice —
  do not add one yourself.
- `recommended: true` highlights your suggested option.

**Response** — `{ success, message?, error?, answers }`. `answers` is keyed by each
question's `header`; each value is the chosen `label`(s) or the custom "Other" text:

```jsonc
{ "success": true, "answers": { "Environment": "Staging" } }
```

---

## `display_resource`

Show a user-facing resource or a rich interaction instead of prose. One tool, many
`type`s.

**Request**

| Field | Type | Applies to | Notes |
| --- | --- | --- | --- |
| `type` | enum | all | `BROWSER`, `FILE`, `TABLE`, `AGENT`, `FUNCTION`, `WORKFLOW`, `APP`, `SCHEDULE`, `WIDGET` |
| `name` | string? | resource types | unique pod resource name; omit to show all of that type |
| `path` | string? | FILE | full pod-visible path (e.g. `/me/reports/q3.pdf`); never a private workspace path (`/tmp`, `/private`, `/Users`) |
| `public_url` | string? | WIDGET | URL to embed/open — exactly one of `public_url`/`content` |
| `content` | string? | WIDGET | inline SVG/HTML fragment (no `<!doctype>`/`<html>`/`<head>`/`<body>`) |
| `loading_messages` | string[]? | WIDGET | ≤4, shown while the widget renders |
| `filters` | RecordFilter[]? | TABLE | `[{ field, op, value }]` — record-API shape |
| `query` | string? | TABLE | read-only SQL, RLS-disabled tables only; mutually exclusive with `filters` |

Validity (enforced in the tool body, returned as `success:false`/`error`, not a hard
failure): BROWSER takes only `type`; `path` is FILE-only; `public_url`/`content`/
`loading_messages` are WIDGET-only; a WIDGET needs **exactly one** of
`content`/`public_url`; `filters`/`query` are TABLE-only and not both; `filters` needs
`name`.

**Per-type, in one line each:**

- `BROWSER` — returns the short-lived URL of the same browser the agent drives with
  browser CLI commands (`type` only).
- `FILE` — show a pod file (`path`). Upload sandbox deliverables first
  (`lemma files upload`).
- `TABLE` — show a datastore table; `name` + optional `filters` (omit `name` to list all
  tables).
- `AGENT`/`FUNCTION`/`WORKFLOW`/`APP`/`SCHEDULE` — show that pod resource (or all of the
  type). Use this after creating/updating a resource instead of only saying you did.
- `WIDGET` — the default for an answer with structure or visual hierarchy beyond
  short prose: several values/records, statuses, steps, comparisons, a timeline,
  compact table, preview, or chart. Before the first widget, load `lemma-widget`.
  Inline widgets are lightweight plain HTML/CSS/JS or SVG and are display-only; use
  a Vite app when the UI needs React, routing, or substantial application state.

**Response** — `{ success, message?, error?, app?, url?, expires_at? }` (`url`/
`expires_at` populate for displayed workspace apps).

---

## `request_approval`

A higher-order gate: ask the user to approve running a tool you lack permission for, then
run it **with the user's authority**. Call it when one of your calls fails with a
permission error (403) or when an action plainly needs the user's say-so (deleting data,
sending email, a privileged command).

**Arguments** (flat, not a nested request object):

| Arg | Type | Notes |
| --- | --- | --- |
| `tool_name` | string | the tool to run on approval (must be one you already have), e.g. `exec_command`, `execute_python`, `pod_write_record` |
| `args` | object | the **complete** arguments for that tool — state everything, don't rely on prior context |
| `title` | string | concise card title |
| `reason` | string? | why this needs approval |
| `payload` | object? | extra structured detail for rendering/audit |

**Response** — `{ success, message?, error?, decision, executed, result, response }`:

- `decision` — `APPROVE_ONCE`, `APPROVE_FOR_SESSION`, or `DENY`.
- `executed` — `true` only when approved and the wrapped tool ran.
- `result` — the wrapped tool's result (run as the user; for CLI/python in a fresh
  workspace session minted with the user's token in the same working directory).
- On `DENY`, nothing runs (`executed:false`).

Pausing tool (conversation → `WAITING`), same resume flow as `ask_user`.

---

## `say` / `listen` (speech)

`say` speaks a reply; `listen` transcribes a voice note. **Text is the default reply
modality** — only `say` when a spoken reply is genuinely wanted (e.g. the user sent a
voice note and expects one back).

**`say`** — request `{ text, output_file_path?, voice? }` → `{ success, message?, error?,
audio_file_path }`. `output_file_path` defaults to `/me/speech/<id>.mp3`. Delivery is
automatic: a native voice note on chat surfaces (OGG voice bubble on Telegram/WhatsApp,
MP3 audio on Slack/others) and an audio player on the web app; the audio is also saved to
the pod datastore.

**`listen`** — request `{ file_path, language? }` → `{ success, message?, error?,
transcript, detected_language?, duration_seconds? }`. `file_path` is a pod path (e.g. an
auto-ingested voice note at `/me/telegram/voice.ogg`) or a workspace path. Common formats
(OGG/Opus, MP3, M4A/AAC, WAV, FLAC, WebM) work directly.

**Behavior rules (the agent must follow these):**

- After `say`, the spoken audio **is** the reply. Do not also write the same words as a
  text message (that duplicates it); a separate text line is fine only if it says
  something *different* (a caption, a link). Assume the user receives and can play it.
- After `listen`, the transcript is for the agent's understanding — act on it. Do **not**
  paste, echo, or rewrite the transcript back ("You said: …").

(These are enforced in the SPEECH capability prompt + the `say`/`listen` tool
descriptions, so any agent with the toolset gets them.)

---

## Building a custom renderer

The current `<lemma-agent-thread>` web component covers the message list, composer,
streaming state, and final reply, but renders tool activity generically. React hooks
(`useConversationMessages`) expose the raw message stream; they do not supply rich
`ask_user`/`request_approval` cards or widget embeds. Build those interaction views in
the product frontend or a custom renderer using this contract:

**1. Read the message stream.** A single assistant turn emits several `role:"assistant"`
messages split by `kind`: `thinking`, `tool_call`, `tool_return`, `notification`, `text`
(the user-facing answer is the `text` message with `metadata.is_final_answer === true` —
see the "one gotcha" in `app-recipes/agent-chat.md`). Each interaction tool appears as a
`tool_call` message (tool name + args, matching the request schemas above) paired with a
`tool_return` message (the response object above). Render from those.

- `display_resource` → render from the `tool_return` (e.g. a FILE card from the resolved
  path, a TABLE view, or — for WIDGET — an embedded iframe, below).
- `say` → render an `<audio>` player from `audio_file_path` (resolve it to a playable URL
  via the SDK files API / file-URL tool). Playback is user-initiated.

**2. Render pending interactions while the conversation is `WAITING`.** When `ask_user` or
`request_approval` pauses, list the pending calls and submit the user's decision:

```http
GET  /pods/{pod_id}/conversations/{conversation_id}/approvals
        # operation agent.conversation.approval.list
        # → pending request_approval AND ask_user tool calls (as messages)

POST /pods/{pod_id}/conversations/{conversation_id}/approvals/{approval_id}/decision
        # operation agent.conversation.approval.resolve
        # body: { "decision": "APPROVE_ONCE" | "APPROVE_FOR_SESSION" | "DENY",
        #         "response": { ... } }     # ask_user answers go under response.answers
        # → records the decision and starts a fresh run that resumes the agent
```

For `ask_user`, put the chosen answers in `response.answers` (keyed by question header).
For an approved `request_approval`, the wrapped tool runs as the user during resume.

**3. Embed widgets.** Mint a short-lived embed URL and load it in an iframe:

```http
POST /pods/{pod_id}/widgets/{conversation_id}/{tool_call_id}/embed-token
        # operation widget.embed_token → { "url": "https://api…/widgets/serve/…?token=…" }
```

---

## See also

- Which toolsets to enable + grants → `agents.md`
- How chat/email delivery works per platform → `surfaces.md`
- Authoring widgets → `lemma-widget/SKILL.md`
- Agent chat UI + raw message shape → `app-recipes/agent-chat.md`
