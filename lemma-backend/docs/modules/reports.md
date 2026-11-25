# Reports module

## Purpose

`app/modules/reports` keeps what a person reports to whoever runs this
deployment: a Lemma's reply they found offensive, harmful or wrong, or another
person's line in a shared chat. It tells that team by email and leaves the
answer to them.

It is what app stores ask of an app that shows AI output and lets people talk
to each other: a way, inside the app, to report what was said. Blocking a
person is the other half, and belongs to the chats themselves (`channels`).

## Runtime contributions and API

The module has no routes. A report is filed from the message it is about, by
the module that holds the message, which checks the reporter may read it and
takes the copy from its own records:

| Route | Owner | Behavior |
| --- | --- | --- |
| `POST /pods/{pod_id}/conversations/{conversation_id}/messages/{message_id}/report` | `agent` | The chat's owner reports a reply of the Lemma's, or a message from somebody outside in a chat the Lemma holds on a platform. Not their own message, and not a tool's output or a notice (422). Anybody else gets 404 |
| `POST /pods/{pod_id}/channels/{channel_id}/messages/{message_id}/report` | `channels` | Anybody who can read the line reports it: a member's, a contact's or an agent's. Not their own, and not an answer withheld from them (422) |

Both take `{reason, note?}` -- `reason` one of `OFFENSIVE`, `HARMFUL`,
`SEXUAL`, `SPAM`, `OTHER`, `note` up to 1000 characters -- and answer 201 with
the report's `id`. Reporting the same message again replaces the reporter's
report and opens it again, keeping the team's `outcome` from last time; the
same `id` comes back.

`contracts.file_report` keeps the report and queues the email in the caller's
unit of work. `events/report_email` is a `delivery` job keyed by the report:
it reads the report, the reporter's name and address, the Lemma and the
organization, and sends one email to `REPORTS_EMAIL` with `Reply-To` set to
the reporter, so the team answers them by replying. Every report ends in one
log line naming it: `reports.email_sent`, or why not. With `REPORTS_EMAIL`
unset, the job still runs, sends nothing, and logs `reports.email_skipped`
with `reason=no_address`, so a deployment that never set it still hears of
each report; with mail not configured, it logs
`reports.email.not_configured.degraded` and sends nothing.

## Storage

| Table | What it holds |
| --- | --- |
| `content_reports` | Who reported, the pod and organization, where (`place`: `CONVERSATION` or `CHANNEL`, with its id and title), the message, who wrote it (`author_kind`, `author_id`, `author_name`), the run for a Lemma's reply, the reason and the words, a copy of the message and up to four messages before it (`excerpt`), and the team's `status`, `closed_at` and `outcome` |

One row per person per message (`uq_content_reports_reporter_message`). It
goes with the pod and with the person who reported. The message, conversation,
channel and run are not foreign keys: removing the message is often what the
team does about a report, and the report stays.

The copy is taken because the team could not read the message otherwise: a
chat with a Lemma opens only for its owner, and a line can be edited or
removed after it is reported. It is the message as the reporter read it: a
line withheld from them, or deleted, says so in the copy.

## Closing a report

Nothing in this repository lists or closes reports: there is no staff page.
The team works from the email, and writes `status`, `closed_at` and `outcome`
on the row when they are done. A page for it belongs with the hosted
service's administration, not here.

## Configuration

| Setting | Meaning |
| --- | --- |
| `REPORTS_EMAIL` | Where each report is emailed. Unset by default: a deployment that sets nothing keeps its reports, sends them nowhere, and logs each (`reports.email_skipped`) |
