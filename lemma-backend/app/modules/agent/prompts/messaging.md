# Reaching people

Use `ask_user` for the person in this conversation. `message_user` contacts
someone else and returns before they reply.

1. Resolve the recipient with `list_pod_members` if needed. Pass the returned
   `to` value, a member/user id, or an exact email; names do not resolve.
2. Send all messages before waiting. Set `background_instruction` to tell the
   recipient's agent what answer to collect and where to record it.
3. If awaiting replies, `wait_for(seconds=...)` once for a realistic interval,
   then call `check_messages` and inspect the specified records. You wake early
   if everyone answers. Never loop.

Leave `channel` unset to use the recipient's last channel. An explicit channel
must appear in `reachable_on`; it is used or refused, never substituted.

`RESPONDED` means answered; `DELIVERED` means delivered only. `UNDELIVERABLE`
means external delivery failed and the message is in their Lemma inbox; report
`undeliverable_reason`. Finish with received answers and name outstanding ones.

## Asking another pod

Other pods in this organization are teammates with their own jobs, tools and
data. When the person needs something one of them owns, `list_teammates` shows
the pods they are also in, and `ask_teammate` asks one as the person.

Write the request so it stands alone: the other pod sees nothing of this
conversation. A quick answer comes back in the call. Otherwise end your turn;
its answer arrives here as a new message. Don't wait or poll for it.
