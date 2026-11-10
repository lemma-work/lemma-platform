---
name: keep-partners
description: Keep the partners table true. Use when a company is added, moves, or signs.
---

# Keep the partners table true

The number Partners owns counts `partners` where `signed` is true. Keep that column as it is.

- Every company you talk to is a row, with its `link` where the conversation is kept, an `owner` and a `stage`. One row per `link`: update it, never add a second.
- After every conversation, set `next_step` and `next_step_due`.
- `signed` is true only once the agreement is signed.
- In a scheduled run, ask owners with `channel_post`: `ask_user` reaches only the person the run belongs to. Ask once per item: check `channel_read(asked_by_me=True)` before asking again.
