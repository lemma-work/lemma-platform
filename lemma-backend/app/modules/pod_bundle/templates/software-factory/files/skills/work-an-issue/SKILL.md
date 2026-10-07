---
name: work-an-issue
description: Take an issue tagged for you to a pull request the team can review, and keep its row in pull_requests. Use when picking up an issue or when asked to fix something in the code.
---

# Work an issue

1. Read the issue and everything it links. If what done looks like is unclear, ask on the issue and stop; do not guess at scope.
2. Make the smallest change that does it, on a branch of its own, with the tests the repo's own changes carry.
3. Open the pull request with what changed, why, and how it was checked. Link the issue.
4. Add a row to `pull_requests`: `title`, `issue`, `link`, `opened_at`, `status` = `open`, `review_rounds` = 0.

Never merge your own pull request and never push to the main branch. When it merges, set `merged_at` and `status` = `merged`. If it is reverted later, set `reverted` to true.
