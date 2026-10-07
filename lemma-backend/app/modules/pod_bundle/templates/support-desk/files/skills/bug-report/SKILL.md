---
name: bug-report
description: Turn customer reports of a problem into a bug report engineering can act on, and keep the known issues list current. Use when a customer reports something broken, or when the same problem shows up in more than one conversation.
---

# Bug report engineering can act on

Before filing, check `known_issues`. If the problem is already there, add one to `reports`, add the customer, update `last_seen`, and reply to the customer with what is known. Do not file it twice.

A new report needs, in this order:

1. **What happens**, in one sentence, in the customer's terms.
2. **Steps to reproduce**, numbered, starting from a state anyone can reach. If you cannot write the steps, ask the customer for them before filing.
3. **What they expected** instead.
4. **Who is affected**: the customers, their plan, how many reports so far.
5. **Evidence**: links to the conversations, screenshots, error text.

File it in the tracker the team uses, then add a row to `known_issues` with the tracker link in `linear_issue`. Never file without steps; ask first.
