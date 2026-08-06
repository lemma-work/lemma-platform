# Reaching people

`message_user` sends to a pod member wherever they actually are — the chat app they last used, or email — and always leaves a copy in their Lemma inbox. Use it to ask a colleague for something, hand work over, or tell the person whose schedule you're running what you found.

**It does not pause your turn, and their reply never comes back as a tool result.** That single fact drives everything below.

## Getting an answer back

1. **Send everything first.** One call per person. Four standup messages are four calls, then you move on — don't send one and wait.
2. **Write a `background_instruction` on anything you need an answer to.** It's never shown to them; it tells the agent handling their reply what counts as an answer and where to put it. "Record their status update as the response summary." "Write the PO number into the `purchase_orders` table, column `po_number`." Without one, their reply is just a chat message and nothing reaches you.
3. **`snooze` once**, for as long as a person realistically takes — ten minutes if they're mid-conversation, an hour or more for a standup that went out at 9am. Not a poll loop: every wake replays this entire conversation, so checking five times costs five times as much and gets the answer no sooner.
4. **`check_messages`** with the ids you were given, plus whatever your instruction told their agent to write.

## Reading the result

`RESPONDED` is the only status that means somebody answered. `DELIVERED` means it reached their phone — people read things and do nothing, which is normal and not a failure.

`UNDELIVERABLE` is also not a failure: no chat app or mailbox could carry it, and it's sitting in their Lemma inbox. Pass on `undeliverable_reason` — it usually means they've never messaged the pod's bot, which is something a human can fix in a minute.

If some are still `OPEN` after you've genuinely waited, say who hasn't answered and finish with what you have. Don't wait on people indefinitely, and don't fill in their answer yourself.

## When not to use it

- **Replying to the person you're already talking to** — just answer them.
- **Asking the person in front of you a question** — that's `ask_user`, which pauses and resumes with their answer. `message_user` is for reaching someone who isn't here.
- **Anything you could look up.** A message costs someone's attention.

## Answering on someone's behalf

When a request is open for the person you're talking to, you'll be told about it, and `respond_to_notification` records what they said. Call it when you actually have the answer — not when they say they'll get to it. Record what they told you and nothing more; a made-up answer is worse than a missing one, because the person who asked will act on it.
