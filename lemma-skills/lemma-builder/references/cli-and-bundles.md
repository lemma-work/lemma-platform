# CLI, Bundles, And Import/Export

The Lemma CLI (`lemma`) is the builder's primary tool. The bundle (a local pod directory) is the unit of work: edit files, import with upsert, test, repeat.

## Auth And Context

```bash
lemma auth login            # browser-based login; stores session per server
lemma auth status
lemma servers list          # multiple backends (cloud/local) supported
lemma orgs list             # select default org
lemma pods list             # marks the currently active pod
```

Config lives in `~/.lemma/config.json` (active server, token, default org/pod). Resolution precedence for org/pod: CLI flags (`--org`, `--pod`) → env vars → project `.lemma.<server>.env` → project `.lemma.env` → stored defaults → interactive selection.

Environment variables (pre-set inside Lemma workspaces/sandboxes — use them, don't bootstrap):
`LEMMA_TOKEN`, `LEMMA_BASE_URL`, `LEMMA_ORG_ID`, `LEMMA_POD_ID`, `LEMMA_SERVER`.

**Develop once, deploy to any server/pod.** A project's server and pod change together (local stack vs. cloud), so the CLI reads committed `.lemma.<server>.env` files keyed by the active server (Vite's `.env.<mode>` model). `lemma app init` and `lemma pods create --with-starter` write them; you can also hand-edit:
- `.lemma.env` — base; may set the folder's default `LEMMA_SERVER`.
- `.lemma.<server>.env` — per-server binding (`LEMMA_POD_ID`, optional `LEMMA_ORG_ID`).

Then `lemma pods describe` uses the folder's default server while `lemma --server lemma-cloud apps deploy` targets cloud — same repo, no global switching. The CLI loads the nearest anchor walking up (ceiling: git root / `$HOME`); active server resolves as `--server` → `LEMMA_SERVER` → base file → config `active_server`. Gitignored `.lemma.<server>.env.local` overrides per-machine; real env always wins (a real `LEMMA_TOKEN`, e.g. agentbox, skips the files). A command that needs a pod on an unbound server fails with `No pod bound for server '<server>'`. `lemma config show` reports the resolved server + applied files. No secrets in these files.

Scripting conventions:

- Default output is a compact, complete table/detail view (schemas included) — prefer it; it costs far fewer tokens than JSON. Use `--output json` (or `--json`) only to pipe/save: `lemma --output json tables list`. Add `--full` to expand folded fields.
- Payloads everywhere take `--data '<json>'` (`-d`) or `--file path.json` (`-f`).
- `--pod <id-or-slug>` overrides the target pod per command.
- Destructive commands prompt; pass `--yes` for agent/CI runs.

## Bundle Format

```text
my-pod/
  pod.json
  tables/<TableName>/<TableName>.json
  functions/<name>/<name>.json    # includes permissions.grants (exported automatically)
  functions/<name>/code.py
  agents/<name>/<name>.json       # includes permissions.grants (exported automatically)
  agents/<name>/instruction.md
  workflows/<name>/<name>.json
  schedules/<name>/<name>.json
  surfaces/<platform>/<platform>.json   # e.g. surfaces/slack/slack.json
  apps/<name>/<name>.json
  apps/<name>/source/            # Vite app project (built on import/deploy)
  apps/<name>/html.html          # OR a single no-build HTML app (uploaded as-is)
  files/<folder>/.folder.json     # folder metadata (file bytes travel with --with-files)
  payloads/                        # local test fixtures, not imported as pod resources
  README.md                        # setup, seed, and verification runbook
```

Rules:

- **Scaffold, don't hand-write.** `lemma <resource> init <name>` writes a near-runnable, commented bundle file with the right shape and folder==name wiring; edit it, then import. `lemma pod init <name>` scaffolds a whole starter pod; `lemma <resource> schema` (or `lemma schema <resource>`) prints the shape for reference. `lemma <agent|function> grant <name> <specs>` merges grants in place, preserving the scaffold's comments.
- **Bundle JSON is JSONC** — `//` and `/* */` comments and trailing commas are allowed (in `.json` files and `$json_file` refs), so scaffolds are self-documenting and forgiving to edit.
- **Folder name must equal the JSON `name` field.** Import validates this.
- Long text/code can live in sidecar files referenced from the JSON:
  - `"code": {"$file": "code.py"}` — raw text file
  - `"instruction": {"$file": "instruction.md"}`
  - `"config": {"$json_file": "config.json"}` — parsed as JSON
- `visibility` (`PERSONAL|POD|RESTRICTED|PUBLIC`, default `POD`) is a first-class field on tables/functions/agents/workflows and round-trips through export/import.
- `pod.json` is metadata plus the portability variables: `{"name": ..., "description": ..., "icon_url": ..., "format_version": 3, "variables": {...}}`.
- `payloads/` and `README.md` are ignored by pod import, but they are part of a high-quality handoff: keep sample function/workflow inputs, account setup JSON, seed records, and the end-to-end smoke test there.

### Per-resource JSON shapes

```jsonc
// tables/tickets/tickets.json  (enable_rls defaults to true / per-user; set false for shared team data — see tables.md)
{ "name": "tickets", "primary_key_column": "id", "enable_rls": false,
  "columns": [ { "name": "title", "type": "TEXT", "required": true } ], "config": {} }

// functions/score_ticket/score_ticket.json — schemas are DERIVED from code headers, never declared here.
// permissions.grants is part of the bundle: export embeds the function's current grants,
// import REPLACES the function's grants with this list (on both create and update).
// Grants are NAME-based: table name, folder path, or connector app id — portable across pods.
{ "name": "score_ticket", "description": "Score a ticket.", "type": "API",
  "code": {"$file": "code.py"},
  "permissions": { "grants": [
    { "resource_type": "datastore_table", "resource_name": "tickets",
      "permission_ids": ["datastore.table.read", "datastore.record.write"] }
  ] } }

// agents/triage-agent/triage-agent.json — same permissions semantics as functions
{ "name": "triage-agent", "description": "Classifies tickets.",
  "instruction": {"$file": "instruction.md"},
  "toolsets": ["POD", "WEB_SEARCH"],
  "permissions": { "grants": [ /* exported + upserted like functions */ ] } }

// workflows/intake/intake.json
{ "name": "intake", "description": "...", "start": {"type": "MANUAL"},
  "nodes": [ /* see workflows.md */ ], "edges": [ ... ] }

// schedules/nightly/nightly.json
{ "name": "nightly", "schedule_type": "TIME", "config": {"cron": "0 2 * * *"},
  "workflow_name": "intake", "is_active": true }

// surfaces/slack/slack.json — folder name is the lowercased platform; one surface per platform
{ "name": "slack", "platform": "SLACK",
  "default_agent_name": "triage-agent",
  "credential_mode": "CUSTOM", "account_id": "${slack_account}",
  "is_enabled": true,
  "config": { "channels": [{"channel_id": "C123", "channel_name": "support"}],
              "identity": {"allowed_domains": ["example.com"]} } }

// apps/ops-app/ops-app.json
{ "name": "ops-app", "description": "...", "public_slug": "ops-app" }

// files/knowledge/.folder.json
{ "description": "Support playbooks", "visibility": "POD" }
```

## Minimal Quickstart Bundle

The smallest thing that imports and does something: one shared table, one agent
granted to read it. Scaffold it, then import.

```bash
lemma pods create demo --org <org>            # import never creates the pod shell
lemma pod init demo                           # writes pod.json + a starter table + agent + AGENTS.md
cd demo
lemma tables init tickets --shared            # tables/tickets/tickets.json (enable_rls:false)
lemma agents init triage                      # agents/triage/{triage.json,instruction.md}
lemma agents grant triage tickets:read,write  # merges into permissions.grants
lemma pods import . --dry-run && lemma pods import .
lemma records create tickets --data '{"title":"smoke","status":"new"}'
lemma agents chat triage "How many open tickets are there?"
```

The on-disk result — folder names equal the JSON `name` of each resource:

```text
demo/
  pod.json
  tables/tickets/tickets.json
  agents/triage/triage.json          + instruction.md     (JSON carries permissions.grants)
  README.md
```

```jsonc
// pod.json
{ "name": "demo", "description": "Ticket triage demo", "format_version": 3 }
// tables/tickets/tickets.json
{ "name": "tickets", "primary_key_column": "id", "enable_rls": false,
  "columns": [
    { "name": "title", "type": "TEXT", "required": true },
    { "name": "status", "type": "ENUM", "default": "new", "options": ["new", "open", "closed"] }
  ], "config": {} }
// agents/triage/triage.json
{ "name": "triage", "description": "Answers questions about tickets.",
  "instruction": { "$file": "instruction.md" }, "toolsets": ["POD"],
  "permissions": { "grants": [
    { "resource_type": "datastore_table", "resource_name": "tickets",
      "permission_ids": ["datastore.table.read", "datastore.record.read", "datastore.record.write"] }
  ] } }
```

Grow it by adding a folder per new resource and re-importing. Connectors, file
bytes, and seed records are added by CLI after import (and recorded in the README).

## Import / Export Semantics

```bash
lemma pods import ./my-pod --dry-run     # validate everything, import nothing
lemma pods import ./my-pod               # upsert by name (default)
lemma pods import ./my-pod/tables/tickets        # single resource
lemma pods import ./my-pod/functions             # one resource collection

lemma pods export ./bundles --force              # export active pod
lemma pods export ./bundles <pod> --resource functions --name score_ticket
lemma pods export ./bundles --exclude apps
```

- **Matching is by `name`** (schedules also match by id; surfaces by platform; files by path). Renaming a resource in the bundle creates a new one — it does not rename.
- **Upsert behavior per resource:** tables → add/remove columns + update config; functions → update description/type/code **+ permissions replaced**; agents → full update except name **+ permissions replaced**; workflows → graph fully replaced; schedules → config/target update; surfaces → upserted by platform (one per platform); apps → metadata update + rebuild/redeploy if `source/` present; files → folders, then file bytes when `--with-files` was used; table rows last, when `--with-data` was used.
- **Permissions travel with the bundle.** Export always embeds each function's and agent's `permissions.grants`; import applies them with replace semantics on every upsert — the bundle is the source of truth for what a workload may access. **Present vs absent matters:** a `permissions` block (even `{"grants": []}`) replaces the grants with exactly that list, while omitting the key leaves the workload's existing grants alone. Grants apply in a deferred pass after every resource exists, so cross-references resolve.
- **Import order is dependency order:** tables → functions → agents → workflows → schedules → surfaces → apps → file folders → file bytes → agent grants → table rows. Agent AND function grants are deliberately deferred to the end, after every resource a grant could name exists.
- **Validation on import:** folder/JSON name match; Python syntax parse; required function headers (`#input_type_name`, `#output_type_name`, `#function_name`, plus `#config_type_name` when a config schema exists); surface platform must be one of SLACK/TEAMS/TELEGRAM/WHATSAPP/GMAIL/OUTLOOK; a Vite app `source/` must build (`npm install && npm run build` → `dist/index.html`), while an HTML app (`source/index.html` with no `package.json`, or a single `html.html`) is uploaded as-is with no build. A grant that references a **table/function/agent/workflow/schedule/app/folder** the bundle neither creates nor finds in the pod is a **hard failure** (the import aborts before any writes), as is a `connector_account` grant naming an account this org can't reach.
- **Advisories (`[yellow]advisory[/yellow]`, non-fatal):** import and `--dry-run` warn — without blocking — about grants that commonly bite: an agent/function **created with NO grants** (it has zero access and will 403 at runtime — the most common way a fresh pod arrives broken), **connector grants** being environment-specific (verify a connected account exists in the target pod), **destructive grants** (e.g. `datastore.table.delete`) giving standing authority with no runtime approval prompt, and a `SUBAGENTS` agent with no `agent` grants being able to spawn only copies of itself. `lemma pods doctor` re-checks all of these against the live pod.
- After import, verify grants landed with `lemma functions permissions get <name>` / `lemma agents permissions get <name>`, or `lemma pods doctor` for the whole pod at once.
- **Unrecognized fields fail the import.** A key the API has no slot for (a typo, or a field that resource type doesn't accept) aborts with the offending name instead of being dropped silently. Outside a bundle the same check warns — `lemma <resource> schema` prints the accepted shape.

## Portability variables

A bundle must not carry ids that mean nothing in another org, so export
**tokenizes** them into `${name}` placeholders and records them under
`pod.json` → `variables`. Three things are tokenized: connector `account_id`
(on any resource), `assignee_pod_member_id`, and an app's `public_slug`.

Export **refuses** to write a bundle that still holds a literal connector account
id, so you cannot accidentally ship one.

Resolve them at import:

```bash
lemma pods import . --var gmail_account=<account-id>      # one at a time
lemma pods import . --values ./vars.json                  # or a file
lemma pods import . --pod-member <member-id>              # fills pod-member vars
```

Pod-member variables default to the importing user's own membership, so a
single-user import usually needs nothing. An **unresolved connector account
variable is dropped** rather than failing the import — the resource lands without
a pinned account and resolves the invoking user's own account at run time, which
is what you want in most pods.

So a surface bundle looks like this, not like a literal uuid:

```jsonc
// surfaces/slack/slack.json
{ "name": "slack", "platform": "SLACK", "credential_mode": "CUSTOM",
  "account_id": "${slack_account}" }
```

## Limits And Gotchas

- Import **does not create the pod** — create/select it first.
- **No in-place column mutation.** Changing a column's type fails; imports only add/remove columns. Treat type changes as a migration (new column, backfill, drop old).
- **Function schemas follow the code** — `input_schema`/`output_schema`/`config_schema` are re-extracted from the Pydantic models on every code-bearing create *or* update, import included. Edit the models and re-import.
- **File bytes and table rows travel only on request.** `pods export --with-files --with-data` (or `--data-table <t>` for specific tables) writes them into the bundle; `pods import --with-files --with-data` applies them. Without those flags you get structure only, and you upload bytes with `lemma files upload` after import.
- **Connectors are not bundle resources.** Auth configs, accounts, and connect state are org runtime state — script their setup (`lemma connectors ...`) in the pod README.
- **Grants are name-based and portable.** `resource_name` is the table name (`tickets`), the stored folder path (a shared `/knowledge` or personal `/me/...` — there is **no** `/pod` prefix), or the connector id (`gmail`). Importing into a different pod resolves names against the target pod, so grants port cleanly as long as the named resources exist there (they do, when they're part of the same bundle). The **one exception is a pinned `connector_account` grant**, whose `resource_name` is an account id: export turns it into a `${…}` variable, and an import that doesn't supply it drops the grant with a warning (the workload falls back to the invoking user's own account). See `authorization-model.md` §4b.
- **Surface bundles carry config, not credentials or platform state.** `account_id` is exported as a `${variable}`, not a literal id (see *Portability variables*); webhook secrets, identities, and setup status are server-managed and re-derived. Modes/event modes use platform defaults on create.
- **No transactions.** Import fails fast on the first error and leaves prior resources applied. Always `--dry-run` first.
- App `public_slug` conflicts are auto-resolved by suffixing a pod-id fragment. The slug is also a portability variable, so you can pin it at import with `--var`.
- System columns `created_at`, `updated_at`, and `user_id` are stripped on import — never declare them. Your primary key (`id`) is yours to declare via `primary_key_column`.

## Command Cheatsheet

```bash
# scaffolding (init-first authoring)
lemma pod init my-pod                       # whole starter pod on disk
lemma table|function|agent|workflow|schedule|surface init <name>   # one resource into the bundle
lemma <resource> schema                     # JSONC shape/example (also `lemma schema <resource>`)

# pods
lemma pods list|get|create|delete|describe|members|export|import|init|doctor
lemma pods create my-pod --with-starter     # create + scaffold + import in one shot
lemma pods doctor [pod]                      # check grants/targets/surfaces wiring
lemma pods export ./bundles --force [--as-template]   # --as-template strips instance data

# data
lemma tables list|get|create|init|add-column|drop-column <name> [...]
lemma tables add-column tickets priority --type INTEGER       # live column add (flags or --data)
lemma records list|get|create|update|import <table> [...]
lemma records import tickets ./seed.csv      # bulk seed from CSV / JSONL / JSON
lemma query run "select status, count(*) from tickets group by status"

# files (natural filesystem) — shared paths are `/…` (e.g. /knowledge); personal is `/me`. No `/pod` prefix.
lemma files ls|tree|cat|stat|write|append|mkdir|upload|download|mv|rm|search|children|child|url|share
lemma files upload ./local.pdf /knowledge/local.pdf
lemma files search "refund policy" --scope /knowledge        # --direct = immediate children; --method TEXT|VECTOR|HYBRID
lemma files cat /knowledge/policy.pdf                         # documents auto-convert to markdown; --pages 2-3
lemma files download /knowledge/policy.pdf ./policy.md --markdown   # save converted markdown
lemma files children /knowledge/policy.pdf                    # derived child files: document.md, figures, pages/page_0001.jpg
lemma files child /knowledge/policy.pdf/pages/page_0001.jpg ./p1.jpg   # fetch one child artifact
lemma files url /knowledge/policy.pdf                         # in-app link (members) + short-lived download url
lemma files share /knowledge/policy.pdf --ttl 3h --max-hits 50   # public, expiring, hit-capped link

# functions / agents
lemma functions list|get|create|update|run|delete|init|grant
lemma functions grant score_ticket tickets:read,write       # edit permissions.grants in the BUNDLE
lemma functions run score_ticket --data '{"title":"x"}'
lemma agents list|get|create|update|delete|chat|run|init|grant
lemma agents init triage [--runtime <profile-id>]     # scaffold; pin a runtime profile
lemma agents grant triage tickets:read /knowledge:read connector:gmail:use   # name:perms | /path:perms | type:name:perms
lemma agents chat triage-agent "Classify this"        # interactive/one-shot chat
lemma agents run triage-agent "Classify this"         # waits + streams result (--no-wait to detach)

# grants on a LIVE pod (same spec grammar; the API only replaces, so add/remove read-merge-write)
lemma agents permissions get <name>
lemma agents permissions add triage tickets:read,write connector:gmail:use
lemma agents permissions remove triage tickets:write
lemma functions permissions add score_ticket function:write_lesson:execute
lemma functions permissions replace <name> --from-bundle ./my-pod   # push what the bundle declares
lemma conversations list|get|messages|send|stream|stop   # each agent run is a conversation

# workflows / schedules
lemma workflows list|get|create|update|update-graph|delete|run|init|validate
lemma workflows validate ./my-pod/workflows/intake    # static graph check before import
lemma workflows run intake --data '{"id":"1"}'        # --data submits to the entry form; waits by default
lemma workflows runs submit-form <run-id> --data '{...}'   # submit the form the run waits on
lemma schedules list|get|create|update|pause|resume|delete|init
lemma schedules create --datastore tickets --on all --workflow intake   # --on all = insert+update+delete

# connectors / surfaces / tools
lemma connectors run gmail "list recent emails" --dry-run    # resolve connector -> op -> input schema
lemma connectors run gmail gmail_list_messages -d '{"max_results":5}'   # ...then run it
lemma connectors list|get|describe|overview|status            # overview = the table of installed auth-configs + accounts
lemma connectors auth-configs list|create|get|delete
lemma connectors accounts list|get|create|delete
lemma connectors connect-requests create <connector> --auth-config-id <id>
lemma connectors operations search|list|get|details|execute [<auth-config-or-connector>] [...]
lemma connectors triggers list|get [<auth-config-or-connector>] [...]
lemma surfaces list|get|upsert|enable|disable|setup|channels|available-channels|delete   # keyed by PLATFORM (slack, gmail, …)
lemma tools list|web-search|report-feedback

# apps
lemma apps list|get|init|create|update|deploy|delete
```

## Verify

After any import:

```bash
lemma pods describe              # full inventory of the pod
lemma <resource> get <name>      # confirm the upsert landed
```

Then run the focused test for whatever you changed (`functions run`, `workflows run`, `agents chat`).

## See also

- The model these resources assemble → `pod-model.md`
- Plan a pod before you author it → `pod-design.md`
- Per-resource JSON + commands → `tables.md`, `files.md`, `functions.md`,
  `agents.md`, `workflows.md`, `schedules-and-triggers.md`, `surfaces.md`, `apps.md`
- Non-bundled setup (connectors, file bytes) → `connectors.md`, `files.md`
- Operate an imported pod → the `lemma-user` skill
