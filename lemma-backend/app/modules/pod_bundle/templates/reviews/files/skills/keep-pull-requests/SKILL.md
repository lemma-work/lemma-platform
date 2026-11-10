---
name: keep-pull-requests
description: Keep the pull_requests table true. Use when a pull request is opened or reviewed.
---

# Keep the pull_requests table true

The number Reviews owns is counted from `pull_requests` by `opened_at` and `reviewed_same_day`. Keep those columns as they are.

- A pull request is opened: add a row with `title`, `repo`, `link` and `opened_at`.
- It gets its first review: set `reviewed_at` and `reviewer`, and `reviewed_same_day` to true when the review came the same working day.

Rows come from where the work happens, through the feed `lemma-setup` sets up. If nothing feeds this table yet, load `lemma-setup` before anything else.
