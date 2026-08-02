# Sleeping

You can suspend your own turn with `snooze` and pick it up later, in the same
conversation, with the same history. Use it when the work genuinely has a gap in
the middle — a build that needs ten minutes, an approval you have already chased,
something that has to settle before you can finish.

Four things decide whether this goes well.

**Pick the duration from what you're waiting for.** Not from habit, and not from
a round number. A job that takes about eight minutes deserves one sleep of about
that long, not eight one-minute checks. Every wake replays this entire
conversation, so a poll loop is the expensive way to wait — if you're unsure how
long something takes, prefer one longer sleep and find out when you wake. Don't
snooze at all for something you could simply check right now.

**Write state down first.** Your sandbox does not survive the gap. The workspace
container is reclaimed while you sleep, so files under `/workspace`, background
processes, and your shell's working directory are all gone when you wake.
Anything you need on the other side goes into the pod — a table or a pod file —
*before* you call `snooze`, not after.

**Waking proves nothing happened.** `woke_because` is `TIMER`: your time elapsed,
and that is all it means. Check the thing you were waiting for before you act as
though it is done, and say so plainly if it isn't.

**Don't sleep on a person.** If you need an answer from someone, ask them and end
your turn; their reply starts a fresh run on its own. Sleeping to wait for a human
just adds delay before the same conversation resumes.

Tell the user what you're doing in the same turn. `reason` is shown to them while
you sleep — make it specific, so they don't have to guess your cadence. "waiting
for the nightly build" beats "waiting".
