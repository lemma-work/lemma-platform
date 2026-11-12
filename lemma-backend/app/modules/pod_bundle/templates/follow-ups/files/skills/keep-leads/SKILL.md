---
name: keep-leads
description: Keep the leads table true. Use when a lead comes in or is followed up.
---

# Keep the leads table true

The number Follow-ups owns is counted from `leads` by `came_in_at` and `followed_up_in_time`. Keep those columns as they are.

- A lead comes in: add a row with `name`, `company`, `link` and `came_in_at`.
- Someone follows up: set `followed_up_at`, and `followed_up_in_time` to true when that was within two days of `came_in_at`.
- Every lead has an `owner`. When nobody is named, ask who it should be: the person in the chat, or, in a scheduled run, whoever holds most leads, with `channel_post`.

Rows come from where the work happens, through the feed `lemma-setup` sets up. If nothing feeds this table yet, load `lemma-setup` before anything else.
