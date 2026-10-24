---
name: keep-conversations
description: Keep the conversations table true. Use whenever a customer writes, is answered, or changes hands.
---

# Keep the conversations table true

The number Support owns is counted from `conversations`, so the table has to match what really happened.

- A customer writes: add a row with `customer`, `about`, `channel`, `link` and `opened_at` (when they wrote).
- Someone answers them: set `answered_at`, and set `answered_in_time` to true when that was within a day of `opened_at`, false when it was later.
- Someone takes it: put their name in `with_person`.

Never mark a row answered because a draft exists. Only a reply the customer received counts.
