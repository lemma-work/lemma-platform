---
name: lemma-user
description: "Operate an existing Lemma (a pod in the CLI) as a person or agent: read what it owns and where its number stands, answer the questions and approvals waiting on you, query tables, search and read files, run functions and workflows, chat with its agents, write in chats and groups, and run connector operations. Do not use for designing or building pods; use lemma-builder instead."
---

# Lemma User

Inside a Lemma, reach tables and files through the `pod_*` tools: they return
structured results, and nothing is cut off by a `| head`; a folder of more
than 500 entries is listed with `lemma files ls` instead. The CLI commands here are for what those tools do not cover, and for
agents outside a Lemma (Claude Code, Codex) that only have the CLI. You are
operating a Lemma that exists; changing its structure is the `lemma-builder`
skill. In code and in the CLI a Lemma is a pod.

## Who you act as

(The full model is `lemma-builder/references/pod-model.md`.)

- **Every call carries a person's identity.** A function or agent runs as the
  person who invoked it, never as a service account, so `/me` and row
  visibility resolve to that person.
- **A workload's authority is the intersection** of its own grants and what the
  invoking person may do. A grant is a ceiling, never a promotion: a viewer who
  invokes an agent granted `datastore.record.write` still cannot write through it.
- **Row security scopes what you see.** On a per-person table (the default) you
  see and edit only your own rows; another person's row is a `404` or missing
  from a list, for admins too. A shared table (`enable_rls: false`) shows
  everyone the same rows. Reading every person's rows needs `mode=ADMIN`, which
  is admin-gated and never the agent's default.
- **`/me` is private; every other path is shared.** `/knowledge`, `/contracts`
  and the like are shared top-level folders. There is no `/pod` prefix. Folder
  grants cascade.
- **Missing access has three shapes.** `INSUFFICIENT_PERMISSION`: the person
  lacks the role. `MISSING_WORKLOAD_RESOURCE_GRANT`: the workload lacks a grant,
  named in the error. `DELEGATION_EXCEEDS_INVOKER`: the workload has the grant
  but the person does not; granting the workload more cannot fix it.

Put deliverables in `/me` or the right shared folder, never only in a local
temp path.

## Orient

```bash
lemma pods list            # marks the active pod
lemma pods describe        # tables, agents, functions, workflows, schedules, apps, surfaces, file tree
                           # (two folder levels; --depth N or --full for more)
```

A workspace injects `LEMMA_TOKEN`, `LEMMA_BASE_URL`, `LEMMA_ORG_ID`,
`LEMMA_POD_ID` and `LEMMA_CONVERSATION_ID`; use them. On a laptop,
`.lemma.<server>.env` files at the project root supply the same values; real
env wins.

- Default output is a compact, complete view (schemas included). Use
  `--output json` (or `--json`) only to pipe or save; `--full` expands folded
  fields.
- Errors, warnings and progress go to stderr, so `lemma --json … | jq` stays
  parseable even when the command fails.
- Payloads: `--data '<json>'` (`-d`) or `--file path.json` (`-f`). Another
  pod: `--pod <id-or-slug>`.
- `--yes` skips a confirmation. Use it only when you know exactly what the
  command touches, and never on a bare `conversations approve`.
- Groups are plural (`lemma files`), most with a singular alias. `query`,
  `datastore`, `runtime`, `servers`, `telemetry`, `auth` and `config` have none.

For multi-step scripts prefer the Python SDK: `from lemma_sdk import Pod;
pod = Pod.from_env()`.

## What this Lemma owns

A Lemma owns one result: a bar held every week (`kind: week`, "every customer
hears back within a day") or a finish line by a date (`kind: date`, with
`due`). It is stored on the pod's config as `config.outcome`:

- `sentence`: what it owns, one line a person can check
- `kind`, `target`, `unit` (`%` means the number reads 0 to 100), `due`
- `measure`: one read-only SQL query over its tables that answers one number,
  counted over every row for everyone alike
- `since`: when it took this on

The agent sets it with its `set_what_you_own` tool; a person with editor rights
can `PUT /pods/{pod_id}/outcome`. The plan's prose (what must be true, how the
work is done, who holds what, what never happens) lives in `/plan.md`, and
lines people teach end with who said it and when. Read the plan before
changing how the work is done.

Reading it:

```bash
lemma pods get --output json        # config.outcome: the sentence, target and measure
lemma files cat /plan.md
```

The number, and changing what it owns:

```bash
lemma pods owns                          # what it owns, and value, measured_at, problem
lemma pods owns get --history 8          # plus the last 8 weeks, oldest first
lemma pods owns set "Every customer hears back within a day" --kind week \
  --target 90 --unit % --measure-file measure.sql   # replaces all of it
lemma pods owns clear --yes              # let it go; its weeks are kept
```

From a script, the SDK: `Lemma.from_env().pods.outcome_standing(pod_id)` and
`.outcome_history(pod_id, weeks=8)`; the API: `GET /pods/{pod_id}/outcome/standing`
and `GET /pods/{pod_id}/outcome/history?weeks=N`. `value` is null when there is no
measure or it could not be read; `problem` says why in plain words. Changing the
measure starts a new history. A measure that counts a per-person table is read
only after someone who administers that table has set it.

## What needs you

Three things wait on a person, in three places.

**A question or approval in a chat with an agent.** An agent's `ask_user`, its
`request_approval`, or a desktop agent's permission request. The conversation
shows `WAITING`.

```bash
lemma conversations approvals <conversation-id>                  # what is open in one conversation
lemma conversations approve <approval-id> -c <conversation-id>   # --deny to refuse; --session allows that kind of call for the rest of it
lemma conversations send <conversation-id> "Use the March numbers."   # answers the one open question
```

With one question open, a message you send is taken as its answer. A message
sent past an open approval records it as denied, so decide approvals with
`approve`. `approve` with no id acts on every pending approval in the
conversation; name the id whenever you mean one.

The CLI has no Lemma-wide list. To see everything waiting on you across your
conversations, longest-waiting first, use `pod.conversations.pending_interactions()`
or `GET /pods/{pod_id}/conversations/pending-interactions` (50 at a time:
follow `next_page_token` for the rest). Each item has the
`conversation_id`, a `kind` (`QUESTION`, `APPROVAL`, `PERMISSION`), a one-line
`summary`, and a `tool_call_id`, which is the approval id `approve` takes
(`POST /pods/{pod_id}/conversations/{conversation_id}/approvals/{tool_call_id}/decision`
in the API). A question is answered there with
`{"decision": "APPROVE_ONCE", "response": {"answers": {"<question header>": "<option label>"}}}`;
an answer outside the options is taken as free text.

A `WAITING` conversation with nothing open is asleep on a timer, a process or a
sub-agent. It resumes by itself within 24 hours and needs nothing from you.

**A question in your chat.** An agent that wants an answer from someone not in
its conversation, or a workflow asking you, writes a line in your own chat with
the Lemma, carried to where you last talked to it (Slack, Telegram, WhatsApp,
email).

```bash
lemma channels inbox                                                   # waiting: questions on you; mentions; contacts waiting
lemma channels messages answer <channel-id> <line-id> --choice Yes     # or --text, --decision APPROVE_ONCE|DENY, --data '<form fields>'
```

Replying where it reached you counts too. A question is answered once; a second
answer is refused.

**A workflow form.** A run parked on a person.

```bash
lemma workflows runs waiting                                    # forms assigned to you
lemma workflows runs get <run-id>                               # active_wait has the form schema
lemma workflows runs submit-form <run-id> --data '{"approved": true}'
```

A form with no assignee is in nobody's queue but any member with execute access
may submit it, so an empty `runs waiting` does not mean nothing is parked.

## Files

Uploaded documents (PDF, Office, Markdown, HTML, EPUB, text) are indexed and
converted to page-marked markdown and page images. Spreadsheets, presentations,
images, email, audio and video are stored but never searchable. Search to find,
`cat` to read, page images to see. Details: `references/files.md`.

```bash
lemma files search "refund policy" --scope /knowledge             # HYBRID; --method VECTOR|TEXT; --direct for children only
lemma files cat /knowledge/handbook.pdf --pages 3-7               # converted markdown, by page
lemma files cat /me/notes/log.md --lines 10-50
lemma files children /knowledge/handbook.pdf                       # derived artifacts
lemma files child /knowledge/handbook.pdf/pages/page_0003.jpg ./p3.jpg
lemma files download /knowledge/handbook.pdf ./handbook.md --markdown

lemma files upload ./report.md /me/reports/report.md               # --no-search to skip indexing
lemma files write /me/notes/draft.md "first line"                  # or append
lemma files ls /knowledge ; lemma files tree /
lemma files stat /knowledge/handbook.pdf                           # indexing status
lemma files retry /knowledge/handbook.pdf                          # after FAILED_PERMANENT, once fixed
lemma files mv /me/notes/draft.md /me/notes/final.md ; lemma files rm /scratch/data.csv

lemma files url /reports/summary.pdf                               # app_url for people in the Lemma
lemma files share /reports/summary.pdf --ttl 3h --max-hits 50      # public, no sign-in, expiring
lemma files shares ; lemma files unshare <code>
```

`cat` stops at about 50,000 characters; `--pages` or `--lines` read a long
document in parts. A fresh upload takes a moment to become searchable.

## Tables, records, query

```bash
lemma tables list
lemma tables get tickets                              # columns, types, enum options
lemma records list tickets --limit 20
lemma records get tickets <record-id>
lemma records create tickets --data '{"title":"New item","status":"new"}'
lemma records update tickets <record-id> --data '{"status":"done"}'
lemma query run "select status, count(*) as total from tickets group by status"
```

Read the schema before writing: ENUM columns refuse values outside `options`,
and a value over 256KB or a record over 1MB is refused (keep the document in a
file and its path in a `FILE_PATH` column). `query run` is one read-only SELECT
across any tables, under the same row security.

**`total` is not a match count.** `query run` returns `{items, total,
truncated}`, stops at the deployment's row cap (1,000 by default), and has no
cursor. When `truncated` is true, `items` is a prefix and `total` is just its
length. Check `truncated` before quoting a number; aggregate with `count(*)`
or narrow the query. (`pod_query` names the same fields `rows`, `row_count`,
`truncated`.)

## Functions, workflows, schedules

```bash
lemma functions list
lemma functions run score_ticket --data '{"ticket_id":"..."}'   # output_data, status, logs
lemma functions runs list score_ticket
lemma functions runs get score_ticket <run-id>                   # function name and run id

lemma workflows list
lemma workflows run intake --data '{"title":"..."}'              # waits by default (--no-wait); --data fills the entry form
lemma workflows runs list intake
lemma workflows runs get <run-id>                                # status, active_wait, step_history, errors
lemma workflows runs cancel <run-id>

lemma schedules list
lemma schedules create --name daily-digest --agent POD_DEFAULT --cron "0 9 * * 1-5" \
  --timezone Europe/Berlin --instruction "..."                   # cron is UTC without --timezone
lemma schedules pause <id> ; lemma schedules resume <id>          # resume resets the failure count
lemma schedules delete <name> --yes
```

The create reply's `next_fire_at` says when it fires; don't create a test
schedule to find out.

**A run's `WAITING` means a person, and only a person.** A run parked on an
agent, an async function, a decision, a timer or its turn in the queue stays
`RUNNING`, so `RUNNING` is not proof anything is executing. `runs get` settles
it: `active_wait.wait_type` is `HUMAN`, `AGENT`, `FUNCTION`, `DECISION`, `TIME`
or `QUEUED`, with the node, assignee and, for a person, the form schema.

## Agents and chat

```bash
lemma chat "What can you do here?"                                  # one message to the default agent
lemma chat                                                          # interactive
lemma agents list
lemma agents chat triage-agent "Summarize today's urgent tickets"   # named agent; interactive without a message
lemma agents run triage-agent "Classify this: ..."                  # waits and streams the answer (--no-wait)
lemma conversations list --agent triage-agent                       # each run is a conversation
lemma conversations list --parent-id <conversation-id>              # what a sub-agent was asked and answered
lemma conversations messages <conversation-id>
lemma conversations send <conversation-id> "Continue with the next batch"
```

`lemma chat` tells a name from a message by whitespace: one quoted multi-word
argument is a message to the default agent, one bare word is an agent name.
Use `--agent` / `--message` when it could be read either way. An agent sees
what you would see: your rows, your `/me`, your connected accounts.

## Chats and groups

Each person has their own chat with the Lemma; each contact has one; groups are
made in Lemma or mirrored from a WhatsApp, Telegram, Slack or Teams group or an
email thread. The CLI calls all of them channels. Lines are numbered per chat
(`seq`).

```bash
lemma channels list --joined                             # --kind DIRECT|CONTACT|GROUP, --assignee me, --status open
lemma channels mine                                      # your own chat with the Lemma
lemma channels messages list <channel-id>                # --before-seq to page back
lemma channels messages post <channel-id> "Shipped."     # --thread <line>; --members-only for a note never sent outside
lemma channels read <channel-id>                         # mark read
lemma channels search "refund policy"                    # every word must match
lemma channels tell sam@example.com "The Q3 report is in /reports."   # a line in their own chat
lemma channels notify <channel-id> MENTIONS              # ALL | MENTIONS | NONE; --via slack, --mute-until
lemma channels join <channel-id>                         # a public group or a contact's chat (leave to go)
lemma channels create "Launch" --private --member <user-id> --agent <agent-id>:EVERY_LINE
lemma channels update <channel-id> --assignee me         # a contact's chat: --status, --assignee none
lemma channels deliveries list <channel-id> <line-id>    # where a line went outside, or why not
```

`tell` takes a member id, user id or email. A private group is seen only by its
members; an admin or editor may join one, and that is recorded. Who sees and
writes in contacts' chats is the Lemma's `contact.read` / `contact.message`.

### Groups with people outside

```bash
lemma channels source whatsapp-group <whatsapp-surface> "Site crew"   # a new WhatsApp group with the bot in it
lemma channels source telegram-link <telegram-surface>                # one-use, hour-long link adding the bot to a group
lemma channels source list <surface>                                  # the groups one bot is in
lemma channels source set <channel-id> <source-id> --no-answers-outsiders   # or --answers-outsiders, --take-over
lemma channels members add <channel-id> …                             # people and agents in a group
lemma channels grants set <channel-id> <table> --read name,status --add name,notes
lemma channels grants list <channel-id> ; lemma channels grants remove <channel-id> <table>
```

People outside the Lemma in a group read only what is Public, and nothing they
say is written anywhere unless the group has a table grant. A grant lets
everybody in the group read the `--read` columns and add rows with the `--add`
columns, through the granting person's access, each row marked as from outside.
An agent's token is refused for `grants set`: a person runs it signed in as
themselves. Per-person tables can't be granted. Setting up group sources and
outsiders is in `lemma-builder/references/people-outside.md`.

### Contacts and form responses

Contacts are people outside the Lemma known by where they wrote from. Answer
them in their chat with `channels messages post`; write first with
`lemma channels outreach <contact-id> <handle> "text"` (email any time,
WhatsApp only within 24 hours of their last message, never after a STOP). A
refused handle posts nothing and says why.

```python
pod.contacts.list()                  # page with next_cursor
pod.contacts.export(contact_id)      # their words and the bot's answers
pod.tables.list_public_rows()        # tables taking rows from outside, and from whom
```

Form responses are rows of the opened table; read them like any records. A new
form is `lemma-form`. `pod.tables.open_public_rows` / `close_public_rows`
changes who may answer. Deleting a contact (`pod.contacts.delete`, admins only)
deletes their rows, handles and conversations too: confirm first.

## Surfaces

A surface is one agent exposed on Slack, Teams, Telegram, WhatsApp or email.
Every agent is minted an email surface when it is created, the default agent
included, so never stand up a mailbox:

```bash
lemma surfaces list                       # what the Lemma answers on, with status
lemma surfaces get resend-assistant       # surface_identity_email: its own address
lemma surfaces setup resend-assistant     # anything still outstanding
lemma surfaces enable resend-assistant    # disable = off, delete = gone
lemma surfaces upsert resend-assistant --data '{"config": {"send_policy": {"allow_send": true}}}'
```

Names default to the platform (`slack`, `resend`); an email surface is
`resend-<agent>`, and `resend-assistant` for the default agent. List before you
name one. Only `ACTIVE` answers. `upsert` with `send_policy.allow_send` lets the
agent send from the Lemma's own address; a Gmail or Outlook connector sends as
the person who connected it. `upsert` changes only the fields passed; creating
or moving a surface is `lemma-builder/references/surfaces.md`.

## Connectors

Inside a Lemma, the connector tools are the short path. From the CLI, `run` is
one call: it picks the operation on a connector slug, one of your own apps, or
a catalog id, and runs it. Never guess a payload; `--dry-run` prints the
schema. More: `references/connectors.md`.

```bash
lemma connectors run gmail "list recent emails" --dry-run       # resolves, prints the input schema
lemma connectors run gmail gmail_list_messages -d '{"max_results": 5}' --metadata-only
lemma connectors run gmail GMAIL_SEND_EMAIL \
  -d '{"recipient_email": "sam@example.com", "subject": "Q3"}' --attach attachment=/me/q3.pdf
lemma connectors pod-list                 # slugs, mode, status
lemma connectors status                   # installed apps and your accounts
lemma connectors overview                 # every installation in the org
lemma connectors describe gmail           # usage guide for that connector
lemma connectors pod-operations gmail "send email"
lemma connectors connect-requests create gmail   # a sign-in link for the person
```

The connector decides whose account runs it, so there is no account to pass.
`CONNECTION_REQUIRED` / `REAUTH_REQUIRED` come with a `connect_url`: hand it to
the person as is and run again when they are done. A write matched from text
rather than named is refused without `--yes`.

## Workspace notes

- Keep one interactive session for long-running processes; one-shot commands
  for everything else.
- A workspace port has no public URL. To show someone an app, deploy it
  (`lemma apps deploy`).
- To keep a web page, use the `browser` skill's `save-webpage <url> --formats
  markdown,pdf`, then upload it.
- On network errors (`Could not resolve host`, TLS timeouts), check
  `curl -sS "$LEMMA_BASE_URL"` once, retry once, then report.

## Troubleshooting

- **Row missing, empty list or `404` on a per-person table.** It is probably
  someone else's row, not lost data.
- **Permission refused.** Read the code. Roles ladder: `POD_VIEWER` reads;
  `POD_USER` also writes records and runs agents, functions and workflows;
  `POD_EDITOR` also changes tables and writes files; `POD_ADMIN` also deletes
  and manages members. `MISSING_WORKLOAD_RESOURCE_GRANT` needs a grant:
  `lemma pods doctor` names workloads holding none, and the fix is
  `lemma agents permissions add <agent> tickets:read,write`.
  `DELEGATION_EXCEEDS_INVOKER` needs the person to get the permission, or a
  different invoker.
- **Not found.** Check the active pod (`lemma pods list`) and exact names
  (`lemma pods describe`, `lemma apps list`).
- **"No such command" / "has no option".** Your CLI may be older than the
  server: `lemma doctor` diagnoses it, `lemma update` upgrades (`--version` to
  pin).
- **Fresh upload not in search.** `files stat`: `NOT_REQUIRED` is not an indexed
  type; `FAILED_PERMANENT` needs the cause fixed, then `files retry`.
- **Numbers look too round.** Check `truncated` before believing `total`.
- **Workflow stuck.** `runs get` → `active_wait` says what it waits on,
  `step_history` shows the failing node, its input and error.

## Report what got in your way

A confusing error, a flag that misbehaves, something you learned by trial and
error, or something this skill got wrong:

```bash
lemma feedback --category cli --subject "…" \
  --issue-encountered "…" --expected-behavior "…" --actual-behavior "…"
```

`--category` is `cli`, `skill`, `platform`, `docs` or `other`.

## See also

- The model → `lemma-builder/references/pod-model.md`
- Build or restructure → `lemma-builder`; set up what a Lemma owns → `lemma-setup`
- Inline views over data → `lemma-widget`
- A browser → `browser`; test an app → `lemma-app-qa`
- Research → `lemma-research`; analysis → `lemma-data-analysis`
- A durable file → `lemma-artifact-author`; parse outside files → `liteparse-documents`
