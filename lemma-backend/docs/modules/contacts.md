# Contacts module

The people a pod's bots answer who are not members. The design, with what is
built and what comes next, is in [Contacts](../../../docs/design/contacts.md).

A contact never signs in, never joins the pod and never holds a grant. Every
person outside the pod that the pod's bots answer is one: somebody writing to
its own bot or address, a stranger asking in one of its groups, a visitor on a
web page. A contact is opened on the first thing they *write* -- a message, a
row, a function call -- and never for reading something.

Most are known by a handle something trustworthy vouched for: a WhatsApp number
in a payload Meta signed, a Telegram user id, a Slack user from another company
in an event Slack signed, an email address the receiving mail service
authenticated. A web visitor nobody has vouched for is an **anonymous**
contact, known only by the browser's chat session (`WEB`, `SESSION`); proving a
handle later -- a code sent to an email, the host's signed token -- makes them
a confirmed contact, and when that handle is an older contact's, the two are
merged into it (`domain/merging`, `services/proofs`): the older vouched contact
survives, the other keeps its row with `merged_into_id`, and everything that
named it is moved -- handles and sessions here, conversations and contact-owned
rows by the `ContactMerged` fact.

## What it owns

| Table | Meaning |
| --- | --- |
| `contacts` | One person per pod, and the name to address them by. `merged_into_id` points a contact that turned out to be another at the one it became (always one hop); `answered_at` is when an agent first answered them, which the organization's contact limit counts. Cascades from the pod |
| `contact_identities` | The handles a contact is known by (`PHONE`, `EMAIL`, `TELEGRAM`, `HOST`, `SLACK`, `TEAMS`, `WEB`) and who vouched for each (`strength`: `CHANNEL`, `HOST`, `CODE`, `MEMBER`, `SESSION`). Only `CHANNEL`, `HOST` and `CODE` prove a handle another contact holds. Unique per pod and handle, so one number is one contact whichever bot it writes to. `last_inbound_at` is when they last wrote from it; `unsubscribed_at` when they asked not to be written to there |
| `visitor_sessions` | One web visitor's chat with one widget, found by a secret only their page holds (stored hashed). Names the visitor's contact from their first write; `strength` says whether anything vouched for them (`ANONYMOUS`, `CODE`, `HOST`). Ends at `expires_at` -- 90 days for an anonymous session, 30 days after last use for a confirmed one, a host session only as long as the host keeps signing -- or when `revoked_at` is set by a reissued widget secret or a forgotten contact. An ended session is swept hourly; its contact and conversation stay |
| `agent_surface_web_codes` | A one-time code sent to an email a visitor typed, hashed with its session; ten minutes, five guesses counted atomically. Swept once used or expired |

- `GET /pods/{pod_id}/contacts` and `GET /pods/{pod_id}/contacts/{id}`: takes
  `conversation.read`. Pages with an opaque `before` / `next_before`
  (`created_at` and id).
- `PATCH /pods/{pod_id}/contacts/{id}`: rename; takes `pod.update`.
- `DELETE /pods/{pod_id}/contacts/{id}`: forget a contact; takes
  `pod.member.manage`. `services/forget.py`: their rows in contact-owned tables
  first (`datastore/contracts/contact_rows.delete_contact_rows`), then one
  main-database transaction for their conversations, handles, web chat
  sessions and codes, and stored platform profiles
  (`agent_surfaces/contracts/contacts`), blanking the questions of theirs that
  reached members' inboxes.
- `GET /pods/{pod_id}/contacts/{id}/export`: a page of their handles, their
  words and the bot's answers (never tool calls or private notes), then their
  rows; follow `next_cursor`. Takes `pod.member.manage`.
- `contracts`: `find_contact`, `open_contact`, `open_anonymous_contact`,
  `prove_handle`, `contact_by_id` (which follows a merge), `contact_handles` (as
  `ContactHandle`), `note_inbound`, `note_answered`,
  `answered_contacts_in_organization` and the two unsubscribes, for the surface
  that resolves a sender and the run that addresses them. `contracts/facts`
  publishes `ContactMerged`.

## What it does not own

- **Deciding who is a contact.** `agent_surfaces` does: a private message on a
  bot that is the pod's own, from somebody who is not a member, when the bot's
  `contacts.answer` is `known` or `anyone` (`services/contacts.py`); somebody
  outside the pod who puts the bot to work in a group it is open to outsiders
  in (`services/outsiders.py`); a web visitor's first message or row
  (`services/web_chat.py`). Somebody writing privately to Lemma's shared bot is
  never one -- they may be signing up. Email that was not authenticated is
  never answered; it is parked as a notification for the member who looks after
  contacts.
- **What a contact's run may do.** `agent` does. A contact's conversation names
  them in its `contact_id` column, which only the server writes, and every run
  in it authorizes as that contact: Public reads, their own rows where they
  alone read the answer, the outsider tool allowlist, in-process runtime only.
- **What is theirs.** `datastore` owns contact-owned tables (`contact_owned`,
  a `contact_id` column and a row policy) and `datastore/contracts/contact_rows`;
  `function` owns `contacts_invoke` and `function/contracts/contact_functions`.
  The agent's `contact_records` and `contact_function` tools take the contact
  from the run, never from the model.
- **What it costs.** `usage` records a contact's run as `contact_run`, charges
  the organization's budget and never the personal allowance of the member who
  looks after the conversation, and holds it to the organization's contacts cap
  (`usage_contacts_caps`): a monthly spend, and how many contacts may be
  answered at all (`max_contacts`, counted by `answered_at`).
