# Contacts module

The people a pod's bots answer who are not members. The design, with what is
built and what comes next, is in [Contacts](../../../docs/design/contacts.md).

A contact never signs in, never joins the pod and never holds a grant. What
makes somebody a contact rather than an outsider is a handle something
trustworthy vouched for: a WhatsApp number in a payload Meta signed, a Telegram
user id, an email address the receiving mail service authenticated.

## What it owns

| Table | Meaning |
| --- | --- |
| `contacts` | One person per pod, and the name to address them by. Cascades from the pod |
| `contact_identities` | The handles a contact is known by (`PHONE`, `EMAIL`, `TELEGRAM`, `HOST`) and who vouched for each (`strength`: `CHANNEL`, `HOST`, `CODE`, `MEMBER`). Unique per pod and handle, so one number is one contact whichever bot it writes to. `last_inbound_at` is when they last wrote from it; `unsubscribed_at` when they asked not to be written to there |
| `visitor_sessions` | One web visitor's chat with one widget, found by a secret only their page holds (stored hashed). Anonymous, or a contact by a host token or a code (`strength`). Ends at `expires_at` -- 90 days for an anonymous chat, 30 days after last use for a contact's, a host session only as long as the host keeps signing -- or when `revoked_at` is set by a reissued widget secret or a forgotten contact. Swept hourly |
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
- `contracts`: `find_contact`, `open_contact`, `contact_by_id`,
  `contact_handles` (as `ContactHandle`), `note_inbound` and the two
  unsubscribes, for the surface that resolves a sender and the run that
  addresses them.

## What it does not own

- **Deciding who is a contact.** `agent_surfaces` does, in
  `services/contacts.py`: a private message, on a bot that is the pod's own,
  from somebody who is not a member, when the bot's `contacts.answer` is
  `known` or `anyone`. Email that was not authenticated is never answered; it is
  parked as a notification for the member who looks after contacts.
- **What a contact's run may do.** `agent` does. A contact's conversation
  carries `audience: contact` and the contact's id, and `answers_outsiders` is
  true of it, so every rule for a group's outsiders holds: anonymous, Public
  reads only, the outsider tool allowlist, in-process runtime only.
- **What is theirs.** `datastore` owns contact-owned tables (`contact_owned`,
  a `contact_id` column and a row policy) and `datastore/contracts/contact_rows`;
  `function` owns `contacts_invoke` and `function/contracts/contact_functions`.
  The agent's `contact_records` and `contact_function` tools take the contact
  from the run, never from the model.
- **What it costs.** `usage` records a contact's run as `contact_run`, charges
  the organization's budget and never the personal allowance of the member who
  looks after the conversation, and holds it to the organization's contacts cap
  (`usage_contacts_caps`).
