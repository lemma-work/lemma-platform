---
name: keep-pull-requests
description: Keep the pull_requests table true. Use when a pull request is opened or reviewed.
---

# Keep the pull_requests table true

- A pull request is opened: add a row with `title`, `repo`, `link` and `opened_at`.
- It gets its first review: set `reviewed_at` and `reviewer`, and `reviewed_same_day` to true when the review came the same working day.
