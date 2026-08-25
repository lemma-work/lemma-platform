# Reaching people

`message_user` returns immediately and their reply never arrives as a tool result. To get an answer back:

0. **Find out who you are messaging.** `to` takes a pod member id, a user id, or an exact email address — a name will not resolve. If you know someone as "Priya", call `list_pod_members` (optionally with a `search`) and pass the `to` value it hands back. The same result carries `reachable_on` — the channels that can carry a message to them right now.
1. **Send every message first** — one call per person, not one-then-wait.
2. **Give each a `background_instruction`.** It is never shown to them; it tells the agent handling their reply what counts as an answer and where to put it — "record their status update as the response summary", "write the PO number into `purchase_orders.po_number`". Without one, their reply is just chat and nothing reaches you.
3. **`snooze` once**, sized to how long a person actually takes — ten minutes mid-conversation, an hour or more for a standup. Not a poll loop: every wake replays this whole conversation. (`snooze` warns against waiting on a person; that rule is about whoever you are already talking to, whose reply starts a fresh run on its own. Nothing resumes you here.)
4. **`check_messages`**, plus wherever your instruction told their agent to write.

**Leave `channel` alone unless you have a reason.** The default reaches someone where they last spoke to you, which is usually where they are looking. Set it when the situation says otherwise — "she's travelling, use WhatsApp", "this needs to be on the record, email it". A channel you name is used or refused, never swapped for another, so pick one from that person's `reachable_on`: a chat app they have never messaged this agent on cannot be used, because bots cannot start a conversation.

`RESPONDED` is the only status that means somebody answered; `DELIVERED` just means it reached their phone. `UNDELIVERABLE` is not a failure — no chat app or mailbox could carry it and it is in their Lemma inbox; pass on `undeliverable_reason`, which usually means they have never messaged the bot.

If some are still open after you have genuinely waited, name who hasn't answered and finish with what you have. Never fill in their answer yourself.

Use `ask_user` for the person in front of you — it pauses and resumes with their answer. `message_user` is for someone who isn't here.
