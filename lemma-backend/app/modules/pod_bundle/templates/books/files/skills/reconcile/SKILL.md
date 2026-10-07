---
name: reconcile
description: Match bank lines to invoices, bills and transfers, and keep the bank_lines table true. Use when new bank lines arrive or when asked what will not tie.
---

# Reconcile

Each bank line is one row in `bank_lines`, added when it is booked: `account`, `description`, `amount`, `booked`, `link`.

1. Match it to the invoice, bill or transfer it settles: same amount, a reference or name that agrees, a date that fits. Write what it matched in `matched_to`.
2. When the match is certain and a person has said matches like it may be recorded without asking, set `reconciled_at`. Otherwise propose the match and wait.
3. When nothing matches, say so with the line, the amount and the closest candidates. Never change an amount to make it tie.
