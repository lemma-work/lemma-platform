---
name: chase-invoices
description: Keep the invoices table current and draft reminders for overdue invoices. Use when an invoice is sent, paid or goes past its due date, or when asked who owes what.
---

# Chase invoices

One row per invoice in `invoices`: `customer`, `number`, `amount`, `currency`, `issued`, `due`, `link`. Set `paid_at` when the payment lands, from the bank line that paid it.

The day after an invoice goes past `due` unpaid, draft a short, polite reminder: the invoice, the amount, the date it was due, and how to pay. A person sends it; set `reminded_at` when it goes out, not when it is drafted. Lead any list of what is owed with the biggest and the oldest.
