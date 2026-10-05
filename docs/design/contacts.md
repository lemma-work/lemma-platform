# Contacts

Status: in progress. Steps 1 and 2 of the [build order](#build-order) are built
(see [Built so far](#built-so-far)); the rest is design.

A pod's bot can answer two kinds of people today: **members**, who act as
themselves, and **outsiders** in a group, who act as nobody. Customer support,
client work, forms and public sites need a third: somebody the pod knows, who is
not a member. They are **contacts**, in code and in the app.

## Decided

- Whether the bot answers people outside the pod is **configuration**, per bot
  surface (see [Who the bot answers](#who-the-bot-answers)).
- "Contact" is the word, everywhere.
- **Contacts are never billed.** Their runs spend the organization's budget,
  under limits the org admin sets.
- Every channel is in scope: forms, web chat, blogs and public sites, WhatsApp,
  email, Telegram.
- Email from a sender authentication doesn't vouch for gets no automatic reply
  (see [Unverified email](#unverified-email)).
- One contacts cap per organization. Anonymous web chats are kept 90 days like
  every other conversation.

## The three people

| | Member | Contact | Outsider |
|---|---|---|---|
| Who | Has a Lemma account in the pod | Known to the pod by a verified handle | Unknown |
| Signs in to Lemma | Yes | **Never** | No |
| Costs a seat | Yes | No | No |
| Authority | Their own | Public reads + their own rows + opted-in functions | Public reads |

A contact is not an account. They never see the Lemma app, never join a pod and
never hold a grant. Contacts belong to one pod. The same phone number writing to
two pods is two contacts. A handle that matches a pod member resolves to the
member, so contacts never shadow members.

## Who the bot answers

Each bot surface (a WhatsApp number, an email address, a Telegram bot, a web chat
widget) carries:

```yaml
contacts:
  answer: off | known | anyone   # default: off
```

- `off`: people who aren't members are refused, as today. Groups keep their own
  `groups.answers_outsiders` setting.
- `known`: answers contacts that already exist and refuses everyone else.
- `anyone`: a stranger becomes a contact with their first message.

Contacts come into existence through:

- A first message to a surface set to `anyone`.
- A verified form, signup or subscription.
- A function holding the `contact.create` grant, such as a signup handler.
- A member adding contacts, or importing a customer table.
- The first verified host token from an embedded web chat.

## Identity

A contact is established by whatever already proved who they are. Each identity
records how it was verified, because authority depends on it.

| Channel | Handle | Proof | Strength |
|---|---|---|---|
| WhatsApp | Phone number | Meta-signed webhook | `channel` |
| Telegram | User id | Signed webhook | `channel` |
| Email | Address | DMARC, or aligned SPF/DKIM, passes (`email_authentication.py`) | `channel` |
| Web chat in the customer's product | Host's user id | Token the host signs with a per-surface secret (HS256, `aud` = surface, `exp` ≤ 10 min) | `host` |
| Forms, public web chat, blogs | Email or phone | One-time code sent to it | `code` |
| Added by a member | Any | The member's word | `member` |

A `member` handle says who a member believes it is, and nothing about who
writes from it, so it never makes a message count as that contact on its own.

- One contact can hold several identities. Identities merge only through proof:
  a contact who verifies a code sent to an email gains that email. A matching
  display name, or an email someone typed, never merges.
- `agent_surface_external_users` stays what it is, a per-platform cache of
  sender profiles. It is not an identity and grants nothing.

### Step-up

`channel` and `host` are enough to read your own order status. They are not
enough to change a payout account. A function opened to contacts declares the
strength it needs (`requires: channel | host | code`). Below that, the run sends
a one-time code to an identity already on file and continues once it's entered.
A code is never sent to a handle the contact typed during the conversation.

## Authority

A contact's run gets a new `ActorType.CONTACT` context, pinned to the pod and
carrying `contact_id`. Like the anonymous context, grants give it nothing.

1. **Public reads**, the same as an outsider's.
2. **Their own rows.** A table opts in as contact-owned: it gets a `contact_id`
   column and a policy `contact_id = current_setting('app.current_contact_id')`,
   alongside the existing per-user policy in `schema_manager`. Contacts read
   these rows and nothing else. They never write tables directly.
3. **Opted-in functions.** Only a function marked `contacts: invoke` is callable
   from a contact's run. It runs as the **function's own workload** with the
   function's grants, never as a member. The platform injects `contact_id` and
   identity strength into its input. The model cannot set or override them, so a
   prompt cannot make a function act for another contact.

Everything else is withheld by the same allowlist and harness gate outsiders use
(`outsider_tools.py`, `outsider_gate.py`): no sandbox, no browser, no connector
accounts, no memory, no sub-agents, no member directory. New tools are refused
until someone classifies them.

**In a group, a contact acts as an outsider.** Everyone in a group sees its
answers, so contact-scoped reads would leak one person's data to the rest.
Contact authority applies only where the conversation is theirs alone.

## Channels

| Channel | How people arrive | Who they are | What they can do |
|---|---|---|---|
| WhatsApp, Telegram | Direct message to the bot | Contact (`channel`) | Converse, own rows, functions |
| Email | Mail to the bot's address | Contact (`channel`) if authentication passes | Converse, own rows, functions |
| Web chat, embedded | Widget in the customer's product: public key + host token | Contact (`host`) | Converse, own rows, functions |
| Web chat, public | Widget on a public site, no token | Outsider until they verify a code, then a contact | Public reads, then upgrade |
| Forms | Public key posting to the widget's function | Anonymous, or a contact (`code`) | One function call |
| Blogs, public sites | Public app pages | Readers are anonymous | Read; ask through web chat; comment or subscribe through a form |

### Web widgets and public keys

Web chat and forms are **web widgets**: one row per chat bubble or form, owned
by a pod and answering as one of its bots. A widget carries:

- **A public key** (`pk_…`). It goes in the page, so anyone can copy it and
  call the endpoint from a script. It names the widget and nothing else: it
  never identifies a person, and whatever it allows, the internet gets. A
  public key allows starting an anonymous chat with the widget's bot, and
  submitting the widget's one form.
- **A signing secret** (`sk_…`), for a widget embedded in the customer's own
  product. It stays on their server, which signs a short-lived token naming its
  signed-in user (HS256, `aud` = the public key, `sub` = their user id,
  `exp` ≤ 10 minutes). The widget passes the token along, and that token -- not
  the key -- makes the visitor a contact (`host` strength). Shown once, stored
  encrypted, rotatable.
- **Allowed origins.** The browser's `Origin` must be one of them. This stops
  other sites embedding the widget, not scripts, so it is a courtesy, not
  security.
- The same `answer` and `looked_after_by` as any bot surface.

Abuse controls from day one: limits per widget, per session and per address,
failing closed; a per-form submission cap; email codes limited per address and
expiring in 10 minutes; and the organization's contacts cap.

Member credentials are never usable from a browser, and a widget's keys are
never a member's.

Notes per channel:

- **WhatsApp.** Free-form replies only within 24 hours of the contact's last
  message. Outside that window Meta requires approved templates (see
  [Outbound](#outbound)).
- **Email.** Replies thread on `Message-ID` and `References`. See
  [Unverified email](#unverified-email) for senders authentication doesn't vouch
  for.
- **Embedded web chat.** A script tag plus one endpoint that exchanges the host
  token for a short-lived chat session. The host's secret is shown once and can
  be rotated.
- **Public web chat.** One conversation per browser session, cookie-bound and
  kept 90 days like every other conversation. Public reads only, rate-limited by session and IP, with bot protection on the
  first message. "Get updates by email" verifies a code and turns the session
  into a contact, keeping the conversation.
- **Forms.** A Public app calls one `contacts: invoke` function. A form that needs
  no reply runs anonymously. A form that does (support request, booking)
  verifies a code first, so the submitter becomes a contact.
- **Blogs.** Pages are a Public app. Comments go through a form function into a
  moderation table, and publishing a comment is a member's or agent's action.
  Subscribing verifies an email and records consent for newsletters.

### Unverified email

An email's `From:` is text the sender chose. It counts only when the receiving
mail service (SES, behind Resend) vouches for it in `Authentication-Results`.
Verification gives **FAIL** for forged or misaligned mail, and **UNKNOWN** when
the sender's domain publishes no SPF/DKIM/DMARC, when forwarding breaks
alignment without ARC, or when Lemma couldn't fetch the message to check.

Neither gets an automatic reply, not even a Public-only one. Any reply goes to
the address in `From:`, so answering a forged message mails whoever the forger
named. That would make Lemma a relay for spam and phishing.
`fallback_reply_service` already stays silent for this reason, and contacts
keep that rule:

- No contact is created and no agent run starts.
- The message lands in the bot's inbox marked *unverified sender*, so a member
  can read it and reply by hand if it's genuine.
- A member can add the address to a contact, for outbound mail and for later
  messages that do authenticate. Unverified mail from it is still parked.
  Someone vouching for an address doesn't make the next forged `From:` genuine.

## Conversations

- One conversation per contact per bot, owned by the member who looks after the
  bot, like the group's `~outsiders` conversation. All of a contact's channels
  feed the same conversation, so a WhatsApp follow-up to an email request has the
  history.
- **Hand-off.** The agent can pass a conversation to a person. A member takes it
  over, replies are sent as the bot, and the agent stays quiet until it's handed
  back. Groups' take-over flow and private notes (`metadata.private_note`) carry
  over unchanged.

## Outbound

A pod may message a contact first ("your order shipped", a newsletter) only with
consent recorded on that identity:

- The contact started a conversation on that channel, or opted in through a form
  or subscription.
- WhatsApp outside the 24-hour window: approved templates only.
- Email: an unsubscribe link and `List-Unsubscribe` on everything not a direct
  reply. Unsubscribing removes consent for that identity.
- Telegram: only after the contact has messaged the bot.

Sending is a member's or workload's action under the `contact.message` grant,
never a contact's.

## Cost and limits

- Contacts are free and never counted as seats.
- Contact runs spend the **organization's monthly budget**. They never touch the
  personal weekly or monthly limits of the member who looks after the
  conversation, because a busy support bot must not use up one person's
  allowance. `MeteringIdentity.user_id` is required today and drives those
  per-user windows. Contact runs need `source_type="contact"` and must skip them.
  Outsider runs in groups should follow the same rule; check what they're
  charged to today.
- The org admin sets one **contacts cap**: monthly, per organization. No per-pod
  or per-surface caps. When it's reached, the bot stops answering contacts, tells them a
  person will reply, and hands the conversations to members.

## Abuse

- Rate limits per contact, per anonymous session and per surface per day, failing
  closed (the `outsider_limits.py` pattern). Strangers get a smaller cap until
  they're contacts.
- Each `contacts: invoke` function has its own call cap, so a contact-facing
  `create_ticket` can't be used to fill a table.
- One-time codes are rate-limited per handle and per IP, and expire in 10
  minutes.
- Every contact turn and function call is audited as `contact:{id}`.

## Personal data

Contacts are personal data the pod holds about its customers. Required from day
one:

- Deleting a contact removes their identities, consent and conversations, and
  deletes or anonymises their contact-owned rows as each table declares.
- A contact's conversations and rows can be exported.
- Conversations are kept 90 days, like the group log, unless the pod sets
  otherwise. Anonymous web-chat conversations follow the same rule.

## Not building

- A contact login to the Lemma app, or a customer portal.
- Contacts shared across pods or across an organization.
- A CRM: no pipelines, deals or contact fields beyond identity, consent and
  display name. Pods model customers in their own tables, keyed by `contact_id`.

## Built so far

- **Contacts and identities.** The `contacts` module owns `contacts` and
  `contact_identities`, with `GET`/`PATCH`/`DELETE /pods/{pod_id}/contacts` and
  `GET .../contacts/{id}/export`. Forgetting a contact deletes their
  conversations in the same transaction; export returns their words and the
  bot's answers, never tool calls or members' private notes. Strengths
  `channel` and `member` exist; `host` and `code` come with web widgets.
- **Who the bot answers.** `config.contacts` on every surface, `answer`
  defaulting to `off`, `looked_after_by` defaulting to whoever turns it on and
  required to be a pod member. Only the pod's own bots answer contacts:
  email always, a chat bot when it has its own account or credentials.
- **Contact runs.** A contact's conversation carries `audience: contact` and the
  contact's id; `answers_outsiders` is true of it, so the outsider rules hold
  unchanged (anonymous, Public reads, the tool allowlist and gate, in-process
  runtime only). The routing link `~contact:{id}` is the second, independent
  record of it. The brief and platform guidance say the chat is private, name
  the contact as a quoted name, and list what is theirs. Audited as
  `contact:{id}`.
- **Their own rows.** `contact_owned` on a datastore table adds `contact_id`
  and a row policy (`app.current_contact_id`): sessions naming no contact see
  every row, a session naming one sees only that contact's. A contact's run
  reads through `contact_records`, which sets the contact from the run, runs as
  the NOBYPASSRLS query role and filters by contact as well. Not combinable
  with per-user `enable_rls`.
- **Functions for contacts.** `PUT /pods/{pod_id}/functions/{name}/contacts`
  opens a function. A contact's run calls it through `contact_function`; it runs
  as its owner's runs do (owner's authority narrowed by the function's grants)
  with the contact's id written into `contact_id` in its input, replacing
  anything the model put there.
- **Unverified email** is parked as an inbox note to the member who looks after
  contacts, once an hour per sender.
- **Cost.** Runs for contacts and group outsiders are recorded as `contact_run`
  and `outsider_run`, skip the member's personal windows, and count towards a
  `contacts_month` window held to `usage_contacts_caps`
  (`GET`/`PUT /usage/organizations/{id}/contacts-cap`, owners and editors).
- **SDKs.** `pod.contacts` and `pod.functions.set_contacts_invoke` in Python;
  `client.contacts` and `functions.setContactsInvoke` in TypeScript.

Not built yet: step-up codes for contact functions (the `requires` strength),
pod bundles carrying `contact_owned` and `contacts_invoke`, and a hand-off
control beyond what `message_user` gives.

## Build order

1. **Contacts and identities.** `pod_contacts`, `pod_contact_identities`,
   `contacts.answer` on surfaces, and resolution from WhatsApp, Telegram and
   authenticated email.
2. **Contact runs.** `ActorType.CONTACT`, the allowlist, metering against the org
   budget and the contacts cap, and hand-off. Public reads only: a working FAQ
   desk on WhatsApp and email.
3. **Contact-owned tables** and `app.current_contact_id`.
4. **`contacts: invoke` functions** with injected identity, one-time codes and
   step-up.
5. **Forms.** Public app to function, anonymous or code-verified.
6. **Web chat.** Embedded with a host token, public with anonymous sessions that
   upgrade.
7. **Outbound and consent.** WhatsApp templates, email unsubscribe, and
   newsletters from blog subscriptions.

Each step ships on its own. Step 2 is a working FAQ desk, and step 5 is the
first thing on the web.
