---
name: keep-controls
description: Keep the controls table true. Use when evidence is added, goes stale, or a control changes hands.
---

# Keep the controls table true

The number Audit owns is the share of `controls` that are `ready`. Keep that column, and the table's name, as they are.

- Every control has an `owner` and a `due_on`.
- A control is `ready` only while `evidence_link` points at evidence dated in `evidence_on` within the last 90 days. Set `evidence_on` to the evidence's own date whenever you set `evidence_link`. When it passes 90 days, set `ready` back to false and ask its owner for new evidence.
- In a scheduled run, ask owners with `channel_post`: `ask_user` reaches only the person the run belongs to. Ask once per item: check `channel_read(asked_by_me=True)` before asking again.
