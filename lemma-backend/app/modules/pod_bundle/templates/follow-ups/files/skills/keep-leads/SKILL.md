---
name: keep-leads
description: Keep the leads table true. Use when a lead comes in or is followed up.
---

# Keep the leads table true

- A lead comes in: add a row with `name`, `company`, `link` and `came_in_at`.
- Someone follows up: set `followed_up_at`, and `followed_up_in_time` to true when that was within two days of `came_in_at`.
- Every lead has an `owner`. Ask who it should be when nobody is named.
