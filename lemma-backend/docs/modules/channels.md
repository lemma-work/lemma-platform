# Channels module

The places a pod's members, contacts and agents talk. A **channel** is one
shared timeline -- who said what, when, and where -- fed by any number of
outside **sources**: a WhatsApp or Telegram chat, a Telegram or WhatsApp group,
a Slack or Teams channel or group DM, an email thread, a web chat -- or by
none, a channel made in Lemma. A **conversation** is one agent
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
| `external_accounts` | An account a pod's bot speaks through -- a Slack team's or Teams tenant's bot, a Telegram bot, an inbox, a WhatsApp number, a web widget -- keyed per pod by platform, `workspace_key` and `bot_key` (`agent_surfaces`' `account_of`: a Slack or Teams account names the workspace *and* the bot, so two agents' apps in one workspace are two accounts; a WhatsApp number is keyed by its phone-number id, a Telegram bot by its id). `bot_key` is NULL until the bot's id is known; when the surface that reached the account learns it, that account is given it in place, so its places, channels and history stay one. A place belongs to an account rather than a surface, so a reinstalled bot -- the same app, so the same bot id, or the same WhatsApp number -- keeps its places; `surface_id` is the installation that reaches it now |
| `channels` | One place per pod: `kind`, `visibility` (`PUBLIC` or `PRIVATE`; only a group may be public), a `title` and `topic`, whose it is (`member_user_id` for `DIRECT`, `contact_id` for `CONTACT`, each unique per pod), when it was archived. A contact's channel says whether its agents answer (`agents_answer`) and how long a member who stepped in holds the contact (`hand_back_after_hours`), and names its current case (`current_case_id`). The timeline's head: `last_seq`, `last_line_id`, `last_line_at`, and how many of its lines are thread replies (`replies`) |
| `channel_cases` | A contact's channel's episodes of being answered, one open at a time (`uq_channel_cases_open`) and the open one always the channel's current case; only a contact's channel has any (the key to the channel carries its `kind`). When it opened (`opened_at`, and `opened_seq`, the line before it), where it stands (`status`: `open`, `waiting_on_contact`, `closed`, with `closed_at` and who closed it), who answers by hand (`assignee_user_id`, `assigned_at`, `assignee_last_line_at`), a call for a person (`attention_at`, `attention_reason`), the acknowledgement of this wait (`acknowledged_at`) and the first member line to the contact (`first_response_at`, `first_response_by_user_id`) |
| `channel_case_events` | Every change to a case, in order: `opened`, `assigned`, `handed_back`, `attention`, `acknowledged`, `first_response`, `status`, `closed`, `reopened`, `merged` -- who made it and what it changed to. Time to first response, reopens and time to close are read from here |
| `channel_members` | Who is in a channel -- a member, a contact or an agent -- with their `role`, how a person hears of it outside the app (`notify`, `notify_via`, `muted_until`) and, for an agent, when it answers (`answers`). A person's seat keeps when they last spoke there and the name they spoke under (`last_spoke_at`, `last_name`): who has spoken in a group is read from the seats, never the lines. Every seat says why it was taken (`seated_by`, below) |
| `channel_read_state` | How far each member has read each channel's timeline, in the channel's pod: `last_read_seq`, and how many of the lines up to it are thread replies (`read_replies`), so unread is exact and counted by number (below). Apart from the seat: a member reads a public group they never joined, and somebody named where they have no read number is given one here, at the channel's head before the line naming them |
| `channel_sources` | The outside places a channel is fed by, in an account, keyed by chat and -- for one person's chat (`kind` `PERSON`, else `CHAT`) -- the person's platform id. Whether the platform keeps it to its members (`is_private`), how to answer it (`delivery_ref`: the reply target of what was last heard there, never what was said) and, for a chat only (a CHECK), who answers for its outsiders, its invite link and the platform's request for it |
| `channel_messages` | The timeline, numbered per channel (`seq`, gapless). `sender_kind` (`MEMBER`, `CONTACT`, `AGENT`) with the sender's id; for an agent, `on_behalf_user_id` or `on_behalf_contact_id` -- whom it answered, and so whose access the answer was made with. `source_id` is where it was heard -- the place a person said it on a platform -- and `target_source_id` where a line written in Lemma (a member's, or an agent's) was carried; never both. Only a heard line says the place is alive (`channel_sources.last_inbound_at`, so a member writing to a contact opens no WhatsApp window). `members_only` marks a note never delivered or shown outside; typed `parts` (mentions, files, links). A heard line keeps its `reply_ref` -- the place, thread, message replied to and id to answer, never the words or the sender's address, number or name. `conversation_id` is the conversation a line was written by (an agent's answer) or heard into (a platform message a surface took up) -- never who answers a line in Lemma, which is `channel_line_answers`. One line per `client_message_id`, and per platform message; a member's is kept in their own namespace (`member:{user_id}:{name}`, `domain.idempotency`) and shown back to them as they sent it |
| `channel_line_answers` | Which agents answer a line: one row per line and agent (`(message_id, agent_id)`; the pod's own agent is the pod's id), so several agents may answer one line, each once. The conversation it answers in, the run (`agent_run_id`, the one its turn started or joined), where it stands (`state`: `ANSWERING`, `ANSWERED`, `ENDED`) and the agent's answer line (`answer_message_id`). Hung from its line by `(message_id, channel_id)` → `channel_messages`, cascading on delete and on update, so it follows a fold |
| `channel_threads` | A line with replies: how many (`reply_count`) and the newest one's time (`last_reply_at`), kept as each reply is written -- the root itself is never rewritten for it. Keyed to the root and its channel (`(root_id, channel_id)` → `channel_messages`), so it follows a fold |
| `channel_changes` | The channel's change log for its live subscribers: one row per change number (`(channel_id, rev)`), naming the line it moved or none for the channel's own fields and members, stamped by the database's clock (`at`). Kept for `revisions.CHANGE_RETENTION` (14 days, `channels.changes_prune` hourly) |
| `channel_mentions` | Who a line names -- a member, a contact or an agent: exactly what its mention parts name now (below). It carries its line's channel and `seq` and, for a member named, when they read it (`read_at`; unread while NULL, and `ix_channel_mentions_user` holds only the unread) -- held to the line's by a cascading foreign key, `(message_id, channel_id, seq)` → `channel_messages`, so they are the line's, never a copy anybody keeps |
| `channel_questions` | A question a line puts to one person, held to its line and channel by `(message_id, channel_id)` → `channel_messages`. What asked it is typed: `asker` (`TOOL` an agent's paused `ask_user` or approval, `WORKFLOW` a workflow's form, `MESSAGE` a question one member's agent put to another), with the asking conversation (`asker_conversation_id`) or workflow run (`asker_workflow_run_id`) and what in it (`asker_key`); `ref` is the asker's idempotency key, unique per pod, never parsed. `awaits_policy` is who may answer (`ONE`: the one person). `kind` is `ASK`, `APPROVAL` or `FORM`, and `state` runs `OPEN` → `ANSWERING` → `ANSWERED`, or `CANCELLED` / `EXPIRED`, with the answer that took effect |
| `channel_message_deliveries` | Every send of a line outside Lemma -- to a place, or a push to a member -- unique per line and target, hung from its line by `(message_id, channel_id)` and carrying the line's `seq`: `PENDING`, `SENT`, `FAILED` (due again after a backoff, no sooner than the platform's `Retry-After`), `SKIPPED` or `DEAD`. Why it is not `SENT` is `reason_code` (`DeliveryReasonCode`), which code matches on, beside `reason`, the sentence a member reads. Claimed by the database's clock, and never while an earlier line of the same channel to the same target has yet to go -- one probe of the target's due rows (`ix_cmd_due_source`, `ix_cmd_due_recipient`); a platform's status report settles it by the id noted the moment the platform answered (indexed as `md5(id)::uuid`). A push stands for the lines `from_seq` .. `seq` of its channel the member heard (`lines` of them; `urgent` when one names or asks them) |
| `channel_delivery_attempts` | Each try of a delivery: when it began and ended, what it ended as (`outcome`, `error_code`) and the platform's status when it gave one -- so a 429 and then a send are two rows |
| `channel_push_pending` | Per member and channel, the lines waiting to be pushed (`from_seq` .. `to_seq`, `lines`, led by `lead_seq`), when they go (`next_attempt_at`) and when the last push went (`pushed_at`) |

A line can be a reply in a thread (`thread_root_id`; a Slack or Teams thread
is one here too), be edited or deleted (it keeps its place, empty), and name
people and agents. An agent's answer to a line in the main timeline is in the
main timeline too, pointing at it (`reply_to_id`), and is carried to a
group's place as a reply to that message there -- a quoted reply on WhatsApp
or Telegram. On Slack and Teams, whose channels thread a bot's answers, it is
a reply in the line's thread, there and here; a line already in a thread is
answered in it (`agent/services/channel_answer_lines.where_the_answer_goes`). A heard line is edited or deleted when its platform says
the message was (Slack `message_changed` / `message_deleted`, Teams
`messageUpdate` / `messageDelete`, Telegram `edited_message`), through the same
`revise_line` / `erase_line` a member's edit takes, and only the line heard as
that message at that place (`agent_surfaces/services/line_changes`).
Who may change a channel may also delete anybody's line in it -- a member's,
a contact's, an agent's -- but never edit it: the line records who did
(`deleted_by_user_id`), every reader sees it as removed by them
(`removed_by`), live readers get it as any delete, and the removal is logged
(`channels.line.removed_by_manager.observed`). A copy already sent outside
Lemma -- to a contact's platform, a Slack channel, a push -- is not recalled. Its thread, the line it replies to and the places it was
heard at or carried to are of its own channel, and the database holds that:
each is a foreign key to `(id, channel_id)` of the line or the source.

Who a line names comes from its parts, through one rule (`Naming`: a member
named must be able to see the channel, a contact must be in it) and one
writer (`infrastructure/mentions.sync_mentions`). A post records them; an
edit makes the rows say what the new parts say -- somebody newly named is
counted and pushed, somebody no longer named stops counting -- and a line
deleted, or emptied because its writer was forgotten, names nobody.

An agent's line names whom it answered (`on_behalf_*`) when it is written,
but no CHECK requires it: forgetting a contact empties
`on_behalf_contact_id` on the answers made for them, and a member's delete
sets `on_behalf_user_id` NULL. Such a line is withheld from every reader but
the owner of the channel it is in (`projection.withheld_from` fails closed).

### What a seat means

A seat in `channel_members` means one of four things, by the channel:

| Channel | A seat is |
|---|---|
| `DIRECT` | its owner: the member whose channel it is, alone |
| `CONTACT` | the contact (`guest`), and each member who writes to them, joined to follow them, or looks after the bot or widget a new place of theirs was heard on (seated with it, when they may see contacts) |
| A private `GROUP` | access: who may read it at all |
| A public `GROUP` | a subscription: whom its lines are pushed to and counted as unread for (anybody in the pod reads it) |

and says why it was taken (`seated_by`): `PLATFORM` (the platform shows them
in the place it mirrors -- they joined it, or spoke there), `SPOKE` (they wrote in it in
Lemma), `ADDED` (a member added them), `CREATOR` (they made it, or it was made
for them), `JOINED` (they joined it themselves). Seated again for a stronger
reason -- the platform's, then speaking in Lemma, then anything the pod chose
-- the seat takes it. Only a `PLATFORM` seat is taken back when the platform
says they left (`MemberRepository.remove_platform_seat`): a member somebody
added stays, whether or not that member is still in the pod. An agent's working -- tool calls, reasoning -- is never a
channel message. It stays in the conversation (`agent_messages`).

## Who sees what

One rule, twice: `domain/access.may_see` and its SQL twin
`infrastructure/access.visible_to`, which every read path uses.

- A `DIRECT` channel is its member's alone.
- A `CONTACT` channel is every member's who holds `contact.read`.
- A `GROUP` is everybody's in the pod when public, its members' when private.
  A group mirrored from a platform is private unless the platform says the
  place is open to its workspace (a Slack public channel, a Teams standard
  channel), and turns private when the platform does -- never back; one made
  in Lemma is public unless made private. One writer decides what a channel's
  places make of it (`services/places.recompute_visibility`, after a place is
  added or turns private): a group any of whose places is private is private.
  Nothing a place says makes a group public again; a member may, through the
  channel's PATCH, only while none of its places is private. A mirrored group's members are the
  pod's people the platform shows are there -- seated as they speak, and out
  when the platform says they left (`contracts/places.left_place`) -- plus
  whoever a member adds, and admins who join on request; those last two stay
  whatever the platform's roster does. The bot taken out of the place -- or
  its app removed from the workspace, which takes it out of every place at
  once (`contracts/places.shared_places_of_surfaces`) -- archives the channel;
  put back, it returns.

### Permissions

On top of seeing a channel, one table (`api/access.py` holds the same one).
`channel.manage` implies `channel.write`, which implies `channel.read`;
`contact.message` implies `contact.read`.

| What | Takes |
|---|---|
| Reading channels, lines, members, threads, deliveries, the inbox, search | `channel.read` |
| One's own channel (`/channels/me`); joining a public group or a contact's channel; leaving; one's own notify settings; marking read | `channel.read` |
| Joining a private group nobody let them into, on request (logged: `channels.member.joined_private_by_right.observed`) | `channel.manage` |
| Answering a question | `channel.read`, and being the person it waits on |
| Writing, editing and deleting one's own lines; starting a group; retrying one's own line's delivery to a place (any line's, for who may change the channel; anybody else reading it is refused, 403); notices | `channel.write` (+ `contact.message` in a `CONTACT` channel) |
| Retrying a push | `channel.write`, and being the member it was to |
| A group's title, topic, visibility, archive, whether it shows agents' answers to everybody (`answers_shared`), delete, members, roles, agents; deleting anybody's line in it (never editing it) | `channel.write` and being its owner -- or `channel.manage` |
| A contact's channel: assignee, status, the agent switch, the hand-back window | `channel.write` + `contact.message` |
| Seeing a `CONTACT` channel at all | `contact.read` |
| A group source's settings (`/channels/{c}/sources/{s}`) | seeing the channel (else 404) or `channel.manage`, and the bot's `agent.read`; changing them, `agent.update` and answering for the group -- or nobody in the pod does, or a pod admin |

| Role | Holds |
|---|---|
| Viewer | `channel.read` |
| User | `channel.write` |
| Editor | `channel.manage`, `contact.message` |
| Admin | everything an editor holds |

Viewers read and answer what is asked of them; users talk in the pod and keep
the groups they make; editors -- who already change the pod's agents and apps
-- keep every group, and are the ones who speak to contacts. Another pod
linked to this one never holds `channel.read` here: a channel carries what
contacts said to this pod.

Within what a reader sees, `domain/projection.py` decides what a run is shown:
an answer an agent made with another member's own access shows only that it
was given, unless it is shared; a contact's run reads only its own source,
never a note.

### Answers made with a member's access

An agent's line made with one member's own access (`used_a_members_access`)
is read in full by that member. Whether the others read it is the channel's:

- **A group shows them to everybody, by default** (`channels.answers_shared`,
  true). Who asked in front of the group asked the group; who wanted it
  private would have asked in their own channel. Such a line is written
  shared -- `shared_at` set as it is posted, by
  `domain/projection.shared_when_posted` in the one write path
  (`LineRepository.append`) -- so a Lemma channel's answer, a platform
  group's reply and any other agent line for a member behave alike, and
  every reader of a line (REST, the socket, search, pushes, another member's
  run) needs no rule of its own: each reads `shared_at`.
- **A group may keep them to whoever asked** (`answers_shared` false, through
  the channel's PATCH, for whoever may change the group). Then such a line is
  `asker_only`: everybody else is shown that it was given, and for whom, not
  what it said (`withheld_from`), until the member it was made for shares it
  (`/messages/{m}/share`, `shareable_by`).
- **A DIRECT or a CONTACT channel** takes no setting (its PATCH refuses it,
  409; it reads `answers_shared: null`). In a member's own channel there is one
  reader anyway; in a contact's an answer made with a member's access is
  withheld from the other members until shared, as before.

The setting applies to lines posted from then on: switching it never shares
or unshares a line already written. An answer nobody can say was for whom is
never shared as it is posted (it fails closed), and no contact reads any of
them, shared or not (`may_read`). Migration `0061_answers_shared` added the
column true, so every group already there shows the answers posted from then
on.

### Tables a group was given

People outside the pod in a group act as nobody: they read what is Public and
nothing else. Whoever may manage a group (its owners, or an admin) can give it
one table at a time (`api/grant_controller.py`, stored by `datastore`'s
`channel_grants`): the columns everybody in the group may read, and the
columns they may fill in a new row. A guest's run reads and adds through
`group_records` and `group_add_record`, as the member who gave it -- their
access rebuilt on every call -- and an added row is marked as from outside,
naming the contact. Everybody in the group reads what is given, so whoever
gives a column gives it to the whole group; a table where each member sees only
their own rows is never given.

## Posting a line

`services/posting.Timeline.post` is the only way a line is written. Under the
channel's row lock, in one transaction, it numbers the line, seats whoever
said it, records who it names and what it asks, moves a contact's case
along (`infrastructure/cases.py`, below), plans its deliveries to the outside
places it is for, and announces it (`ChannelMessagePosted`,
`ChannelDeliveryDue` per delivery). Everything it changes on the channel's
row -- the head, the change counter, a new seat or case moving its
`changed_rev`, a new case's id -- is one `UPDATE channels` at the end of the
post (`revisions.holding`). The row is the pod's hottest: of what a post moves
only `last_line_at` is indexed (the channel list's order), and the table keeps
room on each page (`fillfactor = 80`), so a reply's, a touch's or a seat's
write stays on its page (HOT). Pushes are fanned
out after the commit (`channels.pushes`, below), outside the lock. A line
heard on a platform is written last in the inbound's transaction, after the
turn it starts, so the lock is held from the append to the commit.

A contact's channel is made with its first case. A member writing to the
contact is assigned if nobody is, is the case's first response if nobody has
written yet, and the case waits on the contact; the contact writing opens it
again. A line after the case closed opens the next one, still held by the
member who answered the last (the hand-back window lets them go). Setting the
status or an assignee on a closed case by hand reopens that case, and being
assigned seats the assignee in the channel. A contact held by a member asks
for a reply (`attention`) whenever the contact writes, until the assignee
writes back; a hand-back clears it. A post writes
its case in at most one statement per case table, and a new case's id through
the post's own write to the channel row. Every other case writer takes the
channel's row lock before the case's, as a post does, so two never open two
cases and none waits on another the other way round. A change readers see is
announced as the channel's (`channel.updated`). A group has no case: a
contact's run calling for a person there (`contracts.call_for_person`) flags
nothing in it, and the member who looks after them is told as anywhere else.

Pushes go to a member for: a line naming them; a question waiting on them; a
line in their own channel they did not write; a contact's line in a channel
assigned to them, in it or not; and, set to `ALL`, any line written in Lemma in a group
they are in. `MENTIONS` -- what a group seat starts at; a member's own channel
and a contact's start at `ALL` -- and a note for members keep only the first
two; `NONE` and a mute stop them all. Somebody named or asked who is not in
the channel is pushed too, when they may see it, and so is somebody an edit
names (`pushes.push_named`). Never a line withheld from the member
(`domain/projection.withheld_from`): they could not read it. The first two
*call* the member (`Delivery.urgent`).

`channels.pushes` (`services/pushes.py`, subscribed to `ChannelMessagePosted`,
one at a time per channel) reads every seat, a page at a time in the order
they joined, and folds the line into each hearer's push window for the
channel (`channel_push_pending`, `infrastructure/push_windows.py`). One push
goes per member per channel per window: a line goes at once unless the member
was pushed from that channel in the last `push_windows.PUSH_WINDOW`; then it
waits for the window's end (`channels.push_window`) with whatever else comes
in that time, and they go as one delivery, on the newest line -- or the newest
that calls them -- saying how many it stands for. A line that calls them goes
at once and takes what was waiting with it. What became of a push is on
the delivery it leads: `ChannelMessageResponse.delivered` lists only a line's
sends to places -- shown to everybody who may read the line -- and
`…/deliveries` lists every send of a line, a push only to its recipient.
Sending is `agent_surfaces`' (`services/channel_deliveries.py`): what does not
call a member is skipped once they have read it in the channel, held a few
minutes while they have the app open (and pushed then if still unread), and a
push the member's hourly budget holds back into their window
(`delivery_folded`, `FOLDED`), so it goes with their next push from that
channel.

Agents answer a member's line written in Lemma -- carried to a mirrored
group's place or not -- when it names them, when they answer every line in
the channel, or -- in a member's own channel -- the pod's agent; `agent`
starts one turn per line and agent, and records it (`contracts/answers`,
`channel_line_answers`). The lines a run answers are the ones whose words it
read -- the line that started it and any steered into it -- so a member who
writes a second line while the agent works on the first has the answer under
the first, where they asked; a run carrying on a paused one answers what the
conversation still answers. In a group, the answer follows the line to
its place, into its thread there; in a contact's channel it stays in Lemma
(made with the member's access, it is not the contact's to read). A line heard
on a platform is answered there, by `agent_surfaces`.

What a run is shown of the channel around a line is kept on its message as
which lines (`contracts/backgrounds`: `BackgroundRefs`, line ids and numbers),
never what they said, and read from the timeline whenever its prompt is built
(`read_backgrounds`), as its reader may read them then: an edit reads as
edited, a deleted line or a forgotten contact's is gone, and nothing anybody
said is copied into another conversation to outlive them there. A member's run
in Lemma is shown a contact's lines too, quoted and marked as said by somebody
outside the pod; a member's run in a group on a platform is shown them the
same way, with the bot's answers to them, and not members' notes (the answer is
posted where everybody reads it, and a run that could not see what the room
said answered it wrongly); a contact's run reads what the channel lets a
contact read. A platform's history read live (Slack, Teams)
keeps the lines the pod heard as references, and only what it never heard as
the platform gave it.

## Live

Channels provides the pod live socket's `channel` and `inbox` topics and its
`typing` and `read` calls ([live.md](live.md) has the protocol). Every change a
channel's watchers are shown takes the next number of the channel's change
counter (`channels.last_rev`) under its row lock and appends a row to the
channel's change log (`channel_changes`: the line it moved, or none for the
channel's own fields and members, which also move `channels.changed_rev`) --
`infrastructure/revisions.py`. Nothing rewrites a line to say it moved: a
reply logs its root's change (its count is `channel_threads`'), a delivery's
outcome or a question's claim logs the line's. The counter is taken before the
line or question it covers is touched, in the order a new line takes the
channel lock. A per-process hub per pod (`services/live/hub.py`) hears the
commit's signal and reads the log past its cursor, so a missed signal costs
only latency; its idle poll finds the pod's channels with a change logged
since the last poll, by `(pod_id, at)`. Lines go out through the REST mapper (`message_response`) for each
reader, so the socket shows exactly what a GET would. An agent answering a
line in Lemma says so (`contracts/live.announce_answering`), and the hub
relays the run's tokens to the member it answers as `line.streaming`.

## Questions

A question is answered once, by the person it waits on
(`POST …/messages/{message_id}/answer`, or the agent's `answer_question`).
An approval, and an agent's paused question, only by the person's own hand:
`answer_question` refuses both, and so does the endpoint when called with a
token delegated to an agent. The answer is claimed first (`ANSWERING`), so a
second tap finds it taken; the asker registered for its type
(`contracts/questions.answers_for`) takes it -- `TOOL` an agent's paused
`ask_user` or approval, `WORKFLOW` a workflow's form, `MESSAGE` a question
one member's agent asked another -- and the question closes with what took
effect. If the asker refuses or fails, the question waits again. A claim held
past `CLAIM_TIMEOUT` (its caller died) still waits on its person -- in their
inbox and bell -- may be answered again, and is put back by the sweeper, or
expired if its time ran out meanwhile. Cancelling -- a member leaving the pod,
an asker withdrawing what it asked (`cancel_asked_by`, by the asking
conversation's or run's id, never by matching refs) -- closes every question
still waiting, a few hundred per statement in one transaction, each line
touched before its question (the order an answer takes them in) and each
close announced. A question whose asking conversation or workflow run is
deleted has nobody left to take its answer: the foreign key empties the id,
and the sweeper cancels it (`cancel_orphaned`), announced like any close.

`ChannelQuestionClosed` announces every close, with the asker's type and ids,
and each asker hears its own: an expired `TOOL` question resumes its paused
run as nobody answering (a denial, never an approval -- and the
conversation's own card can no longer approve it); an expired `WORKFLOW` form
ends its run as a form nobody answered; a `MESSAGE` batch -- every question
one run asked -- wakes the asking conversation once that run has finished and
nothing of the batch still waits.

## API

Under `/pods/{pod_id}` -- everybody named one way, a `PersonRef`
(`MEMBER`, `CONTACT` or `AGENT`, an id, a name). PATCH: absent is unchanged,
`null` clears. Creates take an `Idempotency-Key`; GETs never write.

- `/channels` `GET` (kind, `visibility`, `q` -- every word, any order, in
  the title or the name of whose own channel it is --,
  archived, contact, assignee -- `me` or a user
  id --, unassigned, status -- all three a contact's current case's --,
  `needs_person` -- contacts waiting on a person: open, unassigned, the agent
  off or asking for one, read from `ix_channel_cases_needs_person` and
  `ix_channels_agents_off` --, joined,
  `source_surface`; cursor) · `POST`; `/channels/me`
  `GET` · `POST` (the caller's own channel: made when they join the pod, by
  `POST` for a member from before); `/channels/{c}` `GET` · `PATCH`
  (`assignee` takes `me`; a group's `answers_shared`, above) · `DELETE`
  (groups made in Lemma). A contact's
  channel carries its current case -- `case_id`, and its `status`, `assignee`
  and `attention`; no other kind has them. `/channels/{c}/cases` `GET` (a contact's channel:
  its cases, most recently opened first, each with its events; cursor) --
  time to first response, reopens and time to close are read from it.
- `/channels/{c}/members` `GET` (in the order they joined; cursor) · `POST`;
  `/members/me` `POST` (join -- a private
  group too, for `channel.manage`) · `PATCH` (notify, notify_via,
  muted_until) · `DELETE` (leave);
  `/members/{kind}:{id}` `PATCH` · `DELETE`; `/channels/{c}/read`
  `POST {seq, thread_root_id?}` (below).
- `/channels/{c}/messages` `GET` (`before_seq` / `after_seq`) · `POST`;
  `/messages/{m}` `PATCH` · `DELETE`; `/thread` (from its start,
  `after_seq`, or backwards: `newest`, `before_seq`; its root carries its
  live replies' count -- a deleted reply leaves it); `/answer`;
  `/deliveries` `GET` and `/deliveries/{d}/retry`;
  `/files/{file_id}` `GET` (below). Every line a reader may read carries
  `delivered`: each place outside Lemma the line went to, with its status and
  why -- sent again on the socket when a send moves.
- `/channels/{c}/table-grants` `GET`; `/table-grants/{table}` `PUT
  {read_columns, add_columns}` · `DELETE` -- a group only (409 otherwise),
  set by whoever may manage it (below).
- `/inbox` (counts, waiting, mentions, and -- for a reader who sees contacts
  -- `contacts` waiting on a reply: asking for a person, or assigned to them
  with the contact's word last) and `/search` (Postgres full text in
  the channels the caller sees and the lines they read in full -- a withheld
  answer is left out by the query itself --, newest first, each hit with its
  `rank` and a plain-text `snippet`; narrowed by `channel_id`, `kind`,
  `sender_kind` + `sender_id`, `since` / `until`, and `archived` to search
  archived channels too; cursor). Every word must be in the line, matched
  by its start so a search finds as it is typed (`alph` finds `alpha`), and
  both stemmed (`english`: `run` finds `running`) and as written (`simple`:
  `runn` finds `running` too); the stored vector holds both forms. What a
  reader types is only ever words -- `to_tsquery` syntax in it is a
  separator, never an operator. The index keeps the pod beside the words
  (`GIN (pod_id, channel_id, search)`, `btree_gin`, so one channel's matches are found without the pod's), and the lines by when they were said
  (`ix_channel_messages_pod_time`): a common word's newest page is found by
  walking back from the newest line until the page is full, never by finding,
  reading and sorting every match; a rare word through its own entries in
  the text index (`infrastructure/line_search.py`).
  `test_channel_search_scale_e2e.py` holds it to its plan at a hundred
  thousand lines.
- The inbox counts a member's unread mentions from their places in the pod
  -- seats and read numbers -- each one short range of the mention index,
  which holds only the unread: never every mention they were ever given, and
  no line read to count them. A channel muted for now (`muted_until`) is not
  among the channels with something new.
- `/pods/{pod_id}/members/{member}/notices` `POST {text}` (`agent_surfaces`'
  route): tell another member something -- a line from the pod's agent on the
  caller's behalf in the member's own channel, pushed like any of theirs.
  `member` is a pod member id, a user id or an email.

A group source's bot-side settings -- whether people outside the pod are
answered there, who answers for them -- and bringing the bot into a group
are under the channels too, but `agent_surfaces`' routes, since they talk to
the platforms: `/channels/{c}/sources/{s}` `GET` · `PATCH` (`answers_outsiders`,
`owner: "me"`), `/channels/whatsapp-groups` `POST` (the channel is made with
it; `Idempotency-Key`), `/channels/telegram-links` `POST`. They are included
before this module's routers, so the literal segments win over
`/channels/{channel_id}`. The bot's settings page lists its groups with
`GET /channels?source_surface={name}`: each channel carries its
`source_settings`, from the lister `agent_surfaces` registers
(`contracts/source_settings`). A holder of `channel.manage` is listed the
bot's private groups they are not in too -- `joined: false`, with no last line
and nothing unread -- so a pod admin can find a group and join it to take it
over. Every channel row says whether the reader is in it (`joined`).

Creates replay by `Idempotency-Key` -- channels, lines (`client_message_id`
or the key; both sent, they must agree), notices, outreach, a WhatsApp group
start -- and a key is the caller's own: two members who pick one key never
name one line, and no client can take a name the pod's own writers use
(`run:`, `wf:` ...). A line's `client_message_id` comes back to its writer as
they sent it -- the key, when the key named it -- and to nobody else. A replay
shows what it made only to whoever may still see it, and a name or key sent
again for something else (a line's other words, thread or place; a group's
other title, topic or visibility; a notice's other text) is refused, 422,
saying which it was. A line hangs only from threads, lines and places of its
own channel (`posting.LineMisplaced`). An edit naming nothing changes nothing.

Text a person sends -- a line and its parts, a title, a topic, a list's or a
search's words, a notice -- may not hold a NUL character (Postgres text
cannot): 422. Line numbers are bounded by `bigint`. Naming an agent that is
not one of the pod's is refused, 422, as naming somebody who cannot see the
channel is. A member sends at most `NOTICES_PER_MINUTE` (20) notices a minute
in a pod (429, `Retry-After`).

Every refusal is the one error envelope (`ErrorResponse`) with a code to match
on: `CHANNEL_NOT_FOUND`, `LINE_NOT_FOUND`, `MEMBER_NOT_FOUND` (404);
`INSUFFICIENT_PERMISSION` (403); `WRONG_CHANNEL_KIND`, `CHANNEL_ARCHIVED`,
`LAST_OWNER`, `LAST_MEMBER`, `NOT_JOINED`, `CHANNEL_HAS_SOURCES`,
`CHANNEL_CONFLICT` (409); `INVALID_CURSOR` (400); `VALIDATION_ERROR` (422,
the schema's refusals and the module's own alike). The routers document them
(`api.access.ERRORS`).

A group keeps an owner, and a private one somebody in it. Its last owner
leaving hands it to whoever has been in it longest; stepping the last owner
down, or taking them out, is refused (`LAST_OWNER`) until somebody else is
one. The last person in a private group cannot leave it or be taken out
(`LAST_MEMBER`) -- nobody could find it again; it is deleted instead.

### Read state

One read number per member and channel (`last_read_seq`), the timeline's;
threads are read where they are.

- **Unread lines** are the timeline's: lines past `last_read_seq`, less the
  thread replies among them, which count on their thread -- by number, never
  by reading lines (`channels.replies` against `read_replies`). A list's
  last line, and the "channels with something new" badge, follow the same
  rule.
- **Whose they are**: a seat's -- whatever its `notify`, which only decides
  what is pushed -- and, in a channel the member is not in, theirs while a
  line naming them there is unread, counted from the line that named them
  (the read number they were given then). Read, it stops being new to them
  until they are named again. A public group nobody named them in counts
  nothing. Before this, the read number such a member was given was zero,
  which counted nothing while only a seat counted lines; migration
  `0061_answers_shared` moves each one still at zero, with something naming
  them unread, to just before the earliest such line, so it does not count
  the channel's whole history.
- **A mention is read where it was said.** `POST /read {seq}` reads the
  timeline to `seq`: mentions in its top-level lines up to there are read,
  mentions in thread replies are not. `POST /read {seq, thread_root_id}`
  reads that thread -- its root and replies -- to `seq`, and leaves the
  timeline's number where it was. The app sends both: the timeline's when its
  end is on screen, the thread's while a thread is open.
- **Writing reads where one wrote**: a top-level line reads the timeline to
  it; a reply reads its thread to it, never the timeline -- replying is no
  sign of what else one saw.
- **Joining starts at the newest line**: a member seated in a channel for
  the first time (joining, added, a platform seat) reads from its
  `last_seq`. A line that named them before stays unread, and reading the
  timeline to it reads it, though their number is already past it.

## Files on a line

A `file` part names a pod file, and whoever writes the line must be able to
read it (the datastore's own rule, with their access) -- else the line is
refused, 422. It is stored by the file's id (`file_id`) and its own path, not
the `/me/…` it was written as. Readers read it through the line,
`GET …/messages/{m}/files/{file_id}`: whoever may see the line (not where it
is withheld from them), as its writer reads the file -- gone once the line
is, or once the writer can no longer read it. So a file shared in a private
group, a member's own channel or a contact's is read by that channel's
readers and nobody else. The app uploads a channel's attachments to the
writer's own `/me/channels/{channel_id}/`, which no other member can open
directly (`api/attachments.py`). Only raster images are served inline;
anything else is a download.

## Contracts

`contracts/` is what other modules may ask: `source_for` (a place's source,
with its channel made the first time), `post` (the write path above),
`background` (the recent lines a reader may see), `contracts/backgrounds` (a
run's background kept as references, and read), `contracts/answers` (which
agents answer which lines), `contracts/lines` (visibility,
channels, pages, search, a platform thread's root), `contracts/places`
(account-scoped place lookups), `contracts/questions` (the answer registry,
open questions), `contracts/deliveries` (claim, record, retry),
`contracts/contacts` (forgetting a contact, below), and lookups by id. `contracts/facts` publishes `ChannelMessagePosted`, `ChannelDeliveryDue`
and `ChannelQuestionClosed`.

The module subscribes to `ContactMerged`: the merged contact's channel folds
into the survivor's -- whoever survives now, when that contact was merged on
since -- with both channel rows locked in id order. Both channels' lines are
numbered again together, in the order they were said (each channel's own
order kept; at one instant the survivor's first), so the survivor's timeline
reads in order (`infrastructure/fold_lines.py`); what hangs from a line --
its question, mentions, thread, answers, deliveries -- follows it by its
foreign keys, and each member who had read either channel has read the
merged one up to just before the first line they had not read in either.
Sources, seats and the conversations working in it move (where the person
already has a live conversation with that agent there, that one keeps the
seat and the other leaves the channel), a client id both used is renamed on
the moved line, the merged channel's cases join the survivor's (each one's
`opened_seq` counted again), agents answer only if both let them, and the
newer last line shows. Where both had an open case the survivor's goes on,
taking the more recently assigned member, a call for a person either made and
the earlier first response, and the merged one is closed into it (`merged`);
otherwise the open one, or the more recently opened, is current
(`infrastructure/contact_channels.py`, `infrastructure/cases.py`). It also subscribes to `PodMemberAdded` (their own
channel is made, or brought back from the archive) and `PodMemberRemoved`
(they are taken out of its channels, unassigned, their questions cancelled
-- each line touched, each close announced -- and their own channel archived), and
runs two sweeps a minute: questions that expired, whose claim lapsed or whose
asker is gone, and deliveries due again; and one an hour for retention
(below). Every column naming a contact is
moved by the merge -- `contact_channels.RENAMED_ON_MERGE` /
`DEDUPED_ON_MERGE` for the channels' --, which
`contacts/tests/unit/test_merge_covers_every_contact_key.py` holds for every
foreign key to `contacts.id` in the models.

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
- **Sending.** `agent_surfaces` sends and receives: it claims a delivery,
  calls the platform, and records what became of it.

Nothing here is deleted automatically but what was sent: a settled push and
every delivery's tries go `delivery_attempts.KEPT_FOR` (30 days) after they
were made, and an empty push window once its time has passed
(`channels.deliveries_retention`). A send to a place stays as long as its
line: its platform id is how a reply in a thread there finds the line it
answers. The change log a live subscriber resumes from goes after
`revisions.CHANGE_RETENTION` (14 days, `channels.changes_prune`).

**Forgetting a contact** (`contacts`' `DELETE`, through
`contracts/contacts.forget_contacts_in_channels`, in the transaction that
deletes them): their own `CONTACT` channel goes with them -- every line in it
is the pod's conversation with that person. In any other channel their lines
are emptied in place, like a deleted line, and said by nobody (`CONTACT`
with no id): the line keeps its number and its thread, so the members'
replies, the agents' answers and their mentions and questions stay, and the
timeline has no gap. An agent's answer made for them keeps its words and no
longer names whom it was for. Their seats go, and a send of their line not
yet made is `SKIPPED`. The foreign keys back this up: `sender_contact_id`,
`on_behalf_contact_id` and `thread_root_id` are `SET NULL`, never `CASCADE`.
