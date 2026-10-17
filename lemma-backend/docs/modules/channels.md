# Channels module

The places a pod's members, contacts and agents talk. A **channel** is one
shared timeline -- who said what, when, and where -- fed by any number of
outside **sources**: a WhatsApp or Telegram chat, a Telegram or WhatsApp group,
a Slack channel, an email thread, a web chat. A **conversation** is one agent
working for one person -- a member or a contact -- and in a channel there is
one per agent and person, however many threads, numbers or days they write
across.

Three kinds, decided by who the channel is about:

| Kind | Holds |
| --- | --- |
| `DIRECT` | One member and the pod: their private chats on every platform |
| `CONTACT` | One contact and the pod: their private chats, web chat and emails |
| `GROUP` | A chat several people read: a group, a shared channel, an email thread with others on it |

A source joins a channel once, the first time its place is heard, and stays:
a private chat is keyed by the person on it rather than the address it came
to, so a chat that arrives on another pool number is the same source; a group
is keyed by the chat. Nothing infers that two places are one. Replies go back
only to the source they answer -- nothing is mirrored between sources.

In-app chats with an agent, sub-agents and scheduled runs are not in a
channel: they are private workspaces, not shared places.

## What it owns

| Table | Meaning |
| --- | --- |
| `channels` | One place per pod: `kind`, a `title`, whose it is (`member_user_id` for `DIRECT`, `contact_id` for `CONTACT`, each unique per pod), `handler_user_id` -- a member who stepped in, while agents stay quiet -- and `last_message_at` |
| `channel_participants` | The members and contacts in a channel, each with `last_read_at`. Anybody who says something there joins it |
| `channel_agents` | The agents that have answered there. An agent is a sender, never a participant |
| `channel_sources` | The outside places a channel is fed by, keyed by surface, platform, chat and -- for a private chat -- the person's platform id. `surface_id` is kept `SET NULL` so history survives a removed bot. `last_inbound_at` is when somebody last wrote there |
| `channel_messages` | The timeline. `sender_kind` (`MEMBER`, `CONTACT`, `AGENT`) with the sender's id; for an agent, `on_behalf_user_id` or `on_behalf_contact_id` -- whom it answered, and so whose access the answer was made with. `source_id` is where it was said (absent for a line written in Lemma); `members_only` marks a note that is never delivered and never shown outside. A platform message is one line however often it is delivered (unique on source and external message id) |

An agent's working -- tool calls, reasoning -- is never a channel message. It
stays in the conversation (`agent_messages`).

## Who reads what

One rule, in `domain/projection.py`, which every reader asks:

- **A member** reads everything: every source, every note, every answer.
- **A contact** -- a run answering them -- reads only the lines of their own
  source, never a note, and never an answer an agent made with a member's own
  access. An agent line whose principal is no longer known counts as a
  member's, so it fails closed.

## API

`/pods/{pod_id}/channels`:

- `GET` lists the channels the caller may see, most recently active first,
  optionally by `kind`; pages with an opaque `before` / `next_before`.
  Another member's `DIRECT` channel is never listed, and reads as not found.
- `GET /{id}` with its sources and participants; `GET /{id}/messages`, the
  timeline oldest first, paged backwards; `POST /{id}/read` marks it read.
  All take `conversation.read`.
- `PUT /{id}/handler` steps in on a contact's channel and `DELETE` releases
  it; take `contact.message`.

## Contracts

`contracts/` is what other modules may ask: `source_for` (a place's source,
with its channel made the first time), `post` (a line, once per platform
message, announced as `ChannelMessagePosted`), `background` (the recent lines
a reader may see), `speakers_in` and `shared_sources` (the reads behind a
list of groups), `may_read`, and lookups by id. `agent_surfaces` writes every
message it hears and every answer it delivers through them, and reads a group
run's background back.

`contracts/facts` publishes `ChannelMessagePosted`. The module subscribes to
`ContactMerged` (`events/contact_merged_work.py`): a merged contact's channel,
participation and lines move to the contact that survived.

## What it does not own

- **Which place a message came from, and whose it is.** `agent_surfaces`
  decides (`services/channel_places.py`): a private chat belongs to the
  member's or the contact's channel, a group to one of its own.
- **The conversation.** `agent` owns it, and its `channel_id` column;
  `agent/contracts/channel_conversations` finds a person's conversation in a
  channel.
- **Who a contact is.** `contacts` does. A stranger in a group is made a
  contact the first time they speak there, whether or not the bot answers
  strangers, so every line has a sender.
- **Delivery.** `agent_surfaces` sends and receives; a line is written once the
  platform took it.

Nothing here is deleted automatically.
