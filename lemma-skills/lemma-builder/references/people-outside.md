# People outside the pod

A pod answers its **members** as themselves. It can also answer people it does not
let in, who never sign in and are never seats:

- **Contacts** — known by the channel they wrote from: a WhatsApp number or Telegram
  id the platform vouched for, an email address whose domain authenticated it, a
  user your own website signed in, or an email they confirmed with a code.
- **Visitors** — anonymous people on a web chat or a form page.

What they cost comes out of the organization's budget under a monthly **contacts
cap** an org admin sets (`GET/PUT /usage/organizations/{org_id}/contacts-cap`),
never a member's personal allowance.

## What each can reach

| Who | Reads | Writes / runs |
| --- | --- | --- |
| Contact | What the pod made **Public**; their own rows of **contact-owned** tables | Rows of tables **opened** to contacts or to anyone; functions **opened to contacts** |
| Visitor | What the pod made Public | Rows of tables opened to anyone |

A contact's or visitor's run is anonymous to the pod: Public reads only, a short tool
allowlist, no sandbox, no member's grants. In a **group**, people outside the pod are
answered as outsiders the same way (`surfaces.md`).

## The pieces

**A bot that answers contacts.** On a surface that is the pod's own (its own token,
number or mailbox), `config.contacts.answer` decides who it answers privately:

```json
{ "config": { "contacts": { "answer": "anyone" } } }
```

`off` (default): members only. `known`: members and existing contacts. `anyone`: a
stranger becomes a contact with their first message. `looked_after_by` names the
member their conversations belong to (defaults to whoever turns it on). Email whose
sender's domain did not authenticate it is never answered — it waits for a member.

**Contact-owned tables** — rows the pod keeps *about* its contacts (orders, tickets,
bookings). `contact_owned: true` on create or update adds a `contact_id` column and a
row policy: every member sees every row; a contact's run reads only rows naming that
contact (the agent's `contact_records` tool). Not combinable with `enable_rls`.

**Functions opened to contacts** — `PUT /pods/{pod}/functions/{name}/contacts`
`{"contacts_invoke": true}` (SDK `functions.set_contacts_invoke(name, True)`). A contact's run
calls it with `contact_function`; it runs as its owner's runs do, held to its own
grants, and receives the asking contact as `contact_id` in its input — never from the
model. Use it for "look up my order", "book me in".

**Web chat** — a web widget (`/pods/{pod}/web-widgets`, SDK `pod.web_widgets`). Its
`embed` script puts the pod's chat on any site; its `page_url` is a page Lemma hosts.
A visitor becomes a contact by confirming an email code, or when the site's server
signs them in with the widget's secret (an HS256 token living at most ten minutes,
`aud` = the public key, `sub` = their user id, at most 200 characters). A signed-in
chat lasts only as long as the site keeps handing over fresh tokens:
`Lemma.identify(() => fetch("/lemma-token").then(r => r.text()))`. Reissuing the
secret ends every signed-in chat. The public key goes in pages; the signing secret
never does. A new widget answers nobody until `answer` is set, and none answers
anybody unless the deployment has `PUBLIC_WEB_ENABLED` on.

**Forms** — not an object. A **table opened to people outside**
(`PUT /pods/{pod}/datastore/tables/{t}/public-rows`, SDK
`pod.tables.open_public_rows(t, audience=..., columns=[...])`) plus any page that adds
its rows through a widget's key: the hosted `{page_url}?table=t`, the embed script
with `data-lemma-table`, a `<form data-lemma-table>`, or `Lemma.addRow(t, values)`.
Rows are added as the member who opened the table, with only the open columns.

**Reading** — the other way round: a table **opened for reads**
(`PUT /pods/{pod}/datastore/tables/{t}/public-reads`, SDK
`pod.tables.open_public_reads(t, columns=[...], order_by=..., audience=...)`) lets a
page show the chosen columns of every row (`Lemma.readRows(t)`, at most 500). Use it
for what a page must show strangers: free slots, a menu, a price list. Only the pod
writes such a table, usually a function on a schedule. A table is read from outside
or takes rows from outside, never both.
The chat beside the form can fill it in (`fill_form`); it never sends it. Build forms
with the **`lemma-form`** skill.

## Choosing

- Customers ask questions on WhatsApp/email → a surface with `contacts.answer`, the
  answers in Public pages/tables.
- "Where is my order?" → a contact-owned `orders` table.
- "Change my booking" → a function opened to contacts.
- Sign-ups, intake, requests → open a table and give it a page (`lemma-form`).
- Chat on your website → a web widget.
- Work that lives in another system (a helpdesk, a CRM) → still a connector on a schedule.

## Bundles

Contact-owned tables, functions opened to contacts, opened tables and web widgets
are **not carried by pod bundles yet**: set them up again after an import.
