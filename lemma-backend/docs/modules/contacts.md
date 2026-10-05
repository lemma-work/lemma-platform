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
| `contact_identities` | The handles a contact is known by (`PHONE`, `EMAIL`, `TELEGRAM`) and who vouched for each (`strength`). Unique per pod and handle, so one number is one contact whichever bot it writes to |

- `GET /pods/{pod_id}/contacts` and `GET /pods/{pod_id}/contacts/{id}`: any
  member who can read the pod.
- `PATCH /pods/{pod_id}/contacts/{id}`: rename; takes `pod.update`.
- `DELETE /pods/{pod_id}/contacts/{id}`: forget a contact, their handles and
  their conversations, in one transaction; takes `pod.member.manage`.
- `GET /pods/{pod_id}/contacts/{id}/export`: their handles, their words and the
  bot's answers (never tool calls or private notes); takes `pod.member.manage`.
- `contracts`: `find_contact`, `open_contact` and `contact_by_id`, for the
  surface that resolves a sender and the run that addresses them.

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
