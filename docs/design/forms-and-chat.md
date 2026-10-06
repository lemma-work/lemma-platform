# Forms and chat for people outside the space

Status: built, 2026-10-06. Builds on [contacts.md](contacts.md): contacts,
anonymous visitors, web widgets with public keys, email codes.

## Decided

- **A form is not a thing of its own.** It is any page that adds a row to a table
  the space opened to people outside. The table decides who may add rows and
  which columns they may write; the page only asks (Deepak, 2026-10-06, after a
  first build with a form object on the widget was judged a needless new noun).
- **Contacts and anonymous visitors are the primitive.** Forms are a table
  permission, a page, and a skill.
- **Every form has the chat beside it.** The space's web chat is the door a form
  page adds rows through, so the same visitor can ask questions, and the chat
  can fill the form in from what they say. It never sends it for them.
- **Hosted at `forms.lemma.work` later.** Today the hosted page is
  `{api}/public/web/{key}/page?table=…`; the domain maps onto it.

## The pieces

| Piece | What it is |
| --- | --- |
| Table permission | `datastore_public_rows`: who outside may add rows (`anyone`, or confirmed `contacts`), which columns, opened by which member. Rows are added as that member, through the same validation, permission check and insert events as their own hand. `PUT/GET/DELETE /pods/{pod}/datastore/tables/{table}/public-rows` |
| What a page may do | `POST /public/web/{key}/table` (the open columns), `POST /public/web/{key}/rows` (add one row; nothing read back). `contact_id` is stamped on a contact-owned table for a confirmed visitor, never taken from the page |
| The hosted form | `/public/web/{key}/page?table=signups`, drawn by `widget.js` from the open columns; a column's description is its question |
| Any website | the widget script with `data-lemma-table`, or a plain `<form data-lemma-table>`, or `Lemma.addRow(table, values)` |
| Custom design | the `lemma-form` skill: the teammate builds a form app from a template, on the app's own host |
| The chat fills it | `fill_form` on a web visitor's run fills the open columns; the page receives a `fill` frame built from the tool's checked result |
| Collect responses | a table's header action: choose who may answer and which columns, then share the link, the embed code, or your own HTML, or ask the teammate for a custom design |

## Authority

| Who | May |
| --- | --- |
| Visitor | Add one row with the open columns; read nothing back |
| Confirmed visitor | The same, named on a contact-owned table; their conversation |
| Agent on a web visitor's run | Fill the visible form's open columns; the existing contact tools |
| Member | Open a table they can change; the grant goes when they or the table do |

Limits: `surface_web_submissions_per_widget_per_day` per widget and address,
and the organization's contacts cap for any agent work a row starts.

## Not building

- A form object, a form builder, or generated function code for forms.
- Custom markup on the hosted page: a space wanting its own design builds an
  app, which runs on its own origin.
- Multi-page forms and payments in v1.

## Next

- `forms.lemma.work/<id>` mapped onto the hosted page.
- Bot protection before the hosted page opens to everyone.
- "Your requests": a confirmed contact's own rows of a contact-owned table.
