---
name: answer-from-help-center
description: Answer a customer's question from the team's own help center and past replies. Use when a customer asks how something works, why something happened, or what to do next.
---

# Answer from the help center

1. Find the answer before writing. Search the help center pages the team keeps, then past replies to the same question. Quote the policy as it reads today; policies change, so prefer the most recently edited page.
2. Answer the question that was asked, in the customer's words, in three sentences or fewer. Link the help center page when there is one.
3. If two sources disagree, or nothing covers the question, do not guess. Hand over with the hand-over skill.
4. Never promise a refund, a date, a discount or a fix that a person has not already agreed to.
5. Match the team's tone for this customer. Enterprise accounts get a formal reply unless the team has said otherwise.

When the same question has come up three times this week and is not in the help center, say so to the team: it belongs there.

## Keep the conversation's row

Every conversation you work on has one row in `conversations`, which is what you are judged on. Write it from what the helpdesk says, never from memory: `link` to the conversation, `customer`, `channel`, `started_at` when the customer first wrote, `first_reply_at` when the first reply went out, `closed_at` when it closed, `handed_over` true if a person took it, `reopened` true if the customer came back after it closed, and `handled_by` (yourself, or the person who answered). Update the same row as it changes; do not add a second one.
