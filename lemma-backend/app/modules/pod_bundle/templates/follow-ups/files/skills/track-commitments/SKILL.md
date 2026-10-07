---
name: track-commitments
description: Keep the commitments table true to what the team has promised. Use when someone says they will do something for someone, when a promise is kept or dropped, or when asked what is open.
---

# Track commitments

A commitment is a promise with a person on the other end: "I'll send the deck Friday", "we'll get back to you on pricing". Add one row to `commitments` for each:

- `what`: the promise, in a few words.
- `promised_to`: who is waiting on it.
- `owner`: who on the team made it.
- `due`: the date said, or the date implied ("next week" means the following Friday). Leave it empty rather than invent one.
- `source`: a link to where it was said.
- `status`: `open`, then `done` or `dropped`.
- `done_at`: when it was kept, set in the same write that marks it `done`.

Close a row only on evidence: the thing was sent, the person replied, or the owner says it is done. When asked what is open, lead with what is overdue, then what is due this week, owner by owner.
