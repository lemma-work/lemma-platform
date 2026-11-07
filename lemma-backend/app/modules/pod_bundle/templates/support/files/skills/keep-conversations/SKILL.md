---
name: keep-conversations
description: Keep the conversations table true. Use whenever a customer writes, is answered, or changes hands.
---

# Keep the conversations table true

The number Support owns is counted from `conversations` by `opened_at` and `answered_in_time`, so the table has to match what really happened. Keep those columns as they are.

- A customer writes: add a row with `customer`, `about`, where they wrote in `channel`, `link` and `opened_at` (when they wrote).
- Someone answers them: set `answered_at`, and set `answered_in_time` to true when that was within a day of `opened_at`, false when it was later.
- Someone takes it: put their name in `with_person`.
- You draft a reply: set `drafted_at`. A drafted conversation waits on its person; do not draft it again.

Never mark a row answered because a draft exists. Only a reply the customer received counts.

Rows come from where the work happens, through the feed `lemma-setup` sets up. If nothing feeds this table yet, load `lemma-setup` before anything else.
