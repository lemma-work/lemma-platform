# Connectors in detail

`SKILL.md` has the CLI commands. This covers the in-process tools and the
details behind both paths.

## Two ways in

- **Direct tools** (the `CONNECTORS` toolset): in-process, no sandbox. They are
  deferred, so they are not in your prompt prefix: reach them with
  `search_tools` first, then `search_connector_operations` and
  `run_connector_operation`. Prefer these when you have them. Other toolsets
  (sub-agents, messaging, waiting, browser, speech) are deferred the same way.
  Not seeing a tool in your prefix does not mean you lack it.
- **The CLI** (`lemma connectors …`, needs `WORKSPACE_CLI`): the same
  operations through a sandbox round trip. Use it when you are in a shell
  anyway, or for the discovery views.

The authorization is the same either way: a `connector:<slug>:use` grant per
connector, and the call runs as the account that connector picks, the invoking
person's own (per-member) or the one shared with the Lemma (shared). Having the
toolset is not access to any particular app.

## The direct tools

Leave `connector` unset and search by what you want to do. The search spans
every connector you may use, and each hit names the `connector` to run it
through:

```text
search_connector_operations {"query": "send an email"}
  -> [{connector: "gmail", operation: "gmail_send_email", relevance_score: …}, …]

run_connector_operation {"connector": "gmail",
                         "operation": "gmail_send_email",
                         "payload": {"recipient_email": "sam@example.com", "subject": "Hi", "body": "…"}}
```

There is no account argument. Refusals come back as data naming the remedy:
`CONNECTION_REQUIRED` or `REAUTH_REQUIRED` with a `connect_url` to hand to the
person, `CONNECTOR_NEEDS_ACCOUNT` when a shared connector has no shared account,
`CONNECTOR_NOT_INSTALLED` when the person may not install it. Wrong arguments
come back as `invalid_arguments` with the operation's input schema attached:
correct and retry. Pass `connector` on search only to narrow to one;
`describe_connector_operation` only to read a schema up front; `output_path` on
run to land a file result in the Lemma's files.

## Files as arguments

Where a schema takes a file (a Gmail attachment, a Drive or Slack upload, a
multipart field), pass a file reference, never bytes:

```text
run_connector_operation {"connector": "gmail", "operation": "GMAIL_SEND_EMAIL",
  "payload": {"recipient_email": "sam@example.com", "subject": "Q3", "body": "Attached.",
              "attachment": {"pod_path": "/me/reports/q3.pdf"}}}
```

`{"file_id": "..."}` works too, and a list where the field takes several. The
file is read with your access. A file only on your machine goes up first
(`lemma files upload ./q3.pdf /me/reports/`). `describe_connector_operation`
shows which fields take files.

On a native Gmail install, `send_message` and `create_draft` take `to`,
`subject`, `text`/`html` and `attachments` directly. Use them rather than
building MIME for `messages_send`.

## The CLI's `run`

The first argument is a connector slug (`lemma connectors pod-list`), one of
your own installations, or a catalog id; the second is an operation id or plain
English, and the resolved id is printed so you can name it next time.
`--dry-run`, or leaving out `--data` on an operation that needs input, prints
the input schema. `--attach FIELD=PATH` puts a file in a file argument (repeat
it for a list); `@./local.pdf` uploads a local file to `/me/connector-uploads/`
first. `--metadata-only` drops large HTML bodies, which helps when listing mail
or documents. An operation that changes data and was matched from text rather
than named is refused without `--yes`: matching is lexical, so a read intent can
land on a write.

## Apps outside the catalog

Any API, MCP server or Postgres database is added with the CLI and a credential
from a person, through the catalog's generic `openapi` / `mcp` / `sql` entries.
The `lemma-builder` skill's `references/connectors.md` has the commands.
