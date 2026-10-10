---
name: lemma-form
description: "Collect responses from people outside a Lemma pod — sign-ups, intake, requests, surveys, applications, feedback, bookings — by opening a table to outside rows and giving it a page: the hosted form link, a script on a website, or a custom-designed form app with the pod's chat beside it to answer questions and fill it in. Use when someone wants a form, a sign-up or intake page, or to collect answers from customers or the public. Do not use for data entry by the pod's own members (use an app or records via lemma-builder/lemma-user) or for a workflow's approval step (a workflow form node)."
---

# Lemma Form

A form is **not a resource of its own**. It is any page that adds a row to a
table the pod has **opened to people outside**. Two things, always in this order:

1. **The table decides.** Opening it says who may add rows — `anyone`, or only
   `contacts` (people who confirmed their email) — and which columns they may
   write. The server enforces this on every row: a page can never write a column
   that is not open, read rows back, or name somebody else as the sender.
2. **The page only asks.** Lemma's hosted page, a website with the script, or an
   app you design. Customising the look changes nothing about what can be written.

Responses are the table's rows. Anything that reacts to a new row — a workflow
started on insert, a schedule, the teammate — reacts to a response.

## 1. The table

Use an existing table or design one with `lemma-builder` (`references/tables.md`).

- One row per response. Columns are the questions; the column **description** is
  the question text people see (`"Your name"`), so write it for them.
- Types a person can fill: TEXT, INTEGER, FLOAT, BOOLEAN, DATE, DATETIME, ENUM.
  Use ENUM with `options` for a choice. Add a status column with a default
  (`new`) for triage; it stays closed and is filled by the table's default.
- The table must be shared (`enable_rls: false`). A per-member table can't be opened.
- Make it **contact-owned** when you will reply to people or show them their own
  rows later: a confirmed person's row then carries their `contact_id`.

## 2. Open it

As the person asking (they must be able to change the table):

```python
from lemma_sdk import Lemma
pod = Lemma().pod()  # pod from the workspace config
pod.tables.open_public_rows("signups", audience="anyone",
                            columns=["full_name", "email", "company", "seats", "track"])
pod.tables.public_rows("signups")   # what is open, and what could be
pod.tables.close_public_rows("signups")
```

Every column the table needs (required, no default) must be open, or opening is
refused with the reason. Open only what the form asks; never a column meant for
the team (notes, status, owner).

## 3. Give it a page

Forms reach the pod through its **web chat** (a web widget): its public key is
the form's door, and its chat answers questions beside the form. Use the pod's
existing one or make one: `pod.web_widgets.list()` / `pod.web_widgets.create({"name": "Website"})`.
Each widget has `page_url` and `embed`.

- **Fastest — share a link:** `{page_url}?table=signups`. Lemma draws the form from
  the open columns, with the chat beside it. Done in seconds; offer this first.
- **On their website:** the widget's `embed` script with `data-lemma-table="signups"`
  draws the form where the tag is. Optional `data-lemma-title`, `data-lemma-intro`,
  `data-lemma-thanks`, `data-lemma-color`.
- **Their own design:** build an HTML app (`lemma-builder` → `references/apps.md`)
  from `assets/form-app.html`. Mark the form `<form method="post" data-lemma-table="signups">`
  with inputs named after the open columns, or call `Lemma.addRow(table, values)`
  yourself. If the widget lists `allowed_origins`, add the app's origin to it
  (`https://host[:port]`, no path; `http://` only for `localhost`).
  The page holds only the widget's public key. The script keeps the visitor's
  session for itself and sends a short-lived token with every call, so never
  copy one into the page or call `/public/web` yourself.

The script gives the page:

| Call | Does |
|---|---|
| `Lemma.addRow(table, values)` | Adds one row. Rejects with `error.code`: `bad_answer` (message names the field), `needs_contact`, `table_closed`, `rate_limited` |
| `Lemma.describeTable(table)` | The open columns (`name`, `type`, `required`, `options`, `description`) and `contacts_only` |
| `Lemma.readRows(table, {orderBy, desc})` | Every row of a table the pod marked **Public** (at most 500): `{columns, rows}`. Rejects `table_closed` (not Public, per-member, or missing), `bad_order`, `rate_limited` |
| `Lemma.sendCode(email)` / `Lemma.verifyCode(email, code)` | Confirm the visitor's email on your own form, before a contacts-only table takes their row |
| `Lemma.isContact()` | Whether this visitor has confirmed an email |
| `Lemma.onFill(cb)` | The chat filled fields: `cb({table, values})`. `data-lemma-table` forms are filled for you |
| `Lemma.openChat()` | Opens the chat, e.g. for "Questions?" |

A `<form data-lemma-table>` fires `lemma:added` on success and `lemma:error`
(`detail.code`, `detail.message`) otherwise.

## 4. The assistant beside it

On any page with the script, the visitor can talk to the pod's chat: it answers
from what the pod made Public and can **fill the form** from what they say — it
fills fields, they check them and press Send. It never sends for them. To make it
good, put the facts people ask about (dates, prices, policies) in a Public page or
table the chat can read.

## 5. Verify

1. Open the link or app in a browser (`browser` skill), fill it in, press Send.
2. Read the row back (`lemma-user`) — only the open columns are set.
3. Delete the test row. Report the link and what each answer adds.

## Never

- Ask for passwords, card numbers or government IDs in a form.
- Put a widget's signing secret in a page; only its public key belongs there.
- Open the team's own columns to make a form shorter to build.
- Promise a reply channel that isn't set up: a confirmed email is a contact you
  can write to; an anonymous answer has nobody to reply to.
