# Contacts

Status: in progress. Steps 1 to 7 of the [build order](#build-order) are built,
with the gaps listed under [Built so far](#built-so-far).

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

A contact's run gets an `ActorType.CONTACT` context, pinned to the pod and
carrying `contact_id`, built by `build_outsider_context` whichever door they
came in by. Like the anonymous context, grants give it nothing, and neither
passes the pod-membership check.

1. **Public reads**, the same as an outsider's.
2. **Their own rows.** A table opts in as contact-owned: it gets a `contact_id`
   column and a policy that fails closed: a session reading as a member sees
   every row, one naming a contact sees theirs, and one naming nobody sees
   nothing. Every pod session takes its principal from the context
   (`RowPrincipal.of`). Contacts read only the columns a member chose
   (`contact_columns`), and never write tables directly. A contact-owned table
   is never Public.
3. **Opted-in functions.** Only a function marked `contacts: invoke` is callable
   from a contact's run. It runs as the **function's own workload** with the
   function's grants, never as a member: the run has no user, carries the
   contact, and authenticates with a `function-run` token that reaches only its
   pod's data. Its reads of contact-owned tables see that contact's rows only,
   and the rows it adds there are written as that contact.
   The platform writes `contact_id` into its input, replacing whatever the
   model sent. Identity strength is not passed yet (see Step-up).

Everything else is withheld by the same allowlist and harness gate outsiders use
(`ToolPolicy.outsider_allowed`, enforced in `services/tool_runtime.py`): no sandbox, no browser, no connector
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

Web chat is a **web widget**: one row per chat, owned by a pod and answering
as one of its bots. It is also the door a form page adds rows through; a form
itself is any page adding a row to a table opened to visitors (see
[forms-and-chat.md](forms-and-chat.md)). A widget carries:

- **A public key** (`pk_…`). It goes in the page, so anyone can copy it and
  call the endpoint from a script. It names the widget and nothing else: it
  never identifies a person, and whatever it allows, the internet gets. A
  public key allows starting an anonymous chat with the widget's bot, and
  adding rows to the pod's tables opened to visitors.
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
- **Forms.** A page adds a row to a table opened to visitors: anyone, or only
  confirmed contacts. A form that needs no reply runs anonymously. A form that
  does (support request, booking) opens its table to contacts, so the sender
  confirms a code first and their row names them.
- **Blogs.** Pages are a Public app. Comments are rows in a moderation table
  opened to visitors, and publishing a comment is a member's or agent's action.
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
- The member who looks after contacts is told, in their own channel, that
  mail came from an *unverified sender*, so they can read it and reply by hand
  if it's genuine.
- A member can add the address to a contact, for outbound mail and for later
  messages that do authenticate. Unverified mail from it is still parked.
  Someone vouching for an address doesn't make the next forged `From:` genuine.

## Conversations

- One conversation per contact per agent, in the contact's channel, owned by
  the member who looks after the bot. All of a contact's places -- their chats,
  email threads and web chat -- are sources of that channel and feed the same
  conversation, so a WhatsApp follow-up to an email request has the history.
- **Hand-off.** The agent can pass a conversation to a person. A member steps
  in -- the contact's channel is assigned to them (`assignee`) -- replies are
  sent as the bot, and the agent stays quiet until it's handed back, by the
  member or after the channel's hand-back window. Private notes
  (`metadata.private_note`) carry over unchanged.

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
  `GET .../contacts/{id}/export`. Reading contacts -- and their channels -- takes
  `contact.read`, which editors and admins hold (and `contact.message` implies);
  a member who only uses the pod sees neither.
  Forgetting a contact deletes their rows in contact-owned tables first (the
  pod's database), then in one main-database transaction their conversations,
  handles, web chat sessions and codes, and the platform profiles stored for
  their handles; the questions of theirs passed on to members are blanked.
  Export pages (`cursor`) through their words and the bot's answers -- never
  tool calls or members' private notes -- and then their rows. Strengths
  `channel` and `member` exist; `host` and `code` come with web widgets.
- **Who looks after them.** When that member leaves the pod, its
  administrators are told, and the surface carries `contacts_warning` until
  somebody else is chosen. A contact's conversation is found in their channel
  and moves, history and all, to whoever looks after contacts now.
- **Who the bot answers.** `config.contacts` on every surface, `answer`
  defaulting to `off`, `looked_after_by` defaulting to whoever turns it on and
  required to be a pod member. Only the pod's own bots answer contacts:
  email always, a chat bot when it has its own account or credentials.
- **Contact runs.** A contact's conversation carries `audience: contact` and the
  contact's id, read once into the run's `Audience` (`agent/domain/outsiders`),
  which everything the run builds takes its answer from: the CONTACT context,
  the tool allowlist and gate, the brief, the private-note labels and the
  metering scope. A member's note in a contact's chat is labelled as the
  keeper's and never to be repeated. Names
  reach the prompt sanitised and quoted. Audited as `contact:{id}`.
- **Their own rows.** `contact_owned` on a datastore table adds `contact_id`,
  the `contact_columns` a contact may read, and the fail-closed row policy. A
  contact's run reads through `contact_records`, which sets the contact from the
  run, runs as the NOBYPASSRLS query role, filters by contact as well and
  returns only the chosen columns. Record reads by an outsider, or by work done
  for a contact, are refused on such a table; a query reads under the policy.
  Not combinable with per-user `enable_rls`, in any order of PATCH, and never
  Public.
- **Functions for contacts.** `PUT /pods/{pod_id}/functions/{name}/contacts`
  opens a function: its owner, or a pod administrator, and only when its input
  declares `contact_id`. A contact's run calls it through `contact_function`; it
  runs as the function's own workload with no user (`function_runs.user_id`
  null, `contact_id` set), on a `function-run` token, held to its grants and to
  the contact's rows. At most `FUNCTION_CONTACT_CALLS_PER_DAY` calls per
  contact per function; a tool call is never run twice; a run still going at
  the wait deadline is cancelled and reported as such.
  A function that also declares `contact` is told who is asking beyond the id:
  `{id, emails, phones, telegram_ids, host_ids, slack_ids, teams_ids}`, in
  their stored spellings (E.164, lower-cased, `host:{issuer}:{sub}`), so it can
  match an order or a project by the address it was placed under. Only handles
  something proved count -- `channel`, `host` or `code`, never one a member
  typed in or a browser session -- and whatever the model put under `contact`
  is dropped, declared or not. A merged contact is told as the survivor (its
  `id` and handles); `contact_id` stays what the conversation named. `contact`
  is `null` for an anonymous form submission. A handle the contact said stop
  on is still listed: a stop is about the pod writing to it, not about whose it
  is, and outreach checks it on its own.
  What such a run writes is the contact's, not a member's: every row event it
  raises, insert, update or delete, on any table, is marked from outside and
  names `contact:{id}`, as a form row does. A DATASTORE schedule ignores it
  unless it asks for `include_outside_rows`, and when it does, its LLM filter
  and the run it starts are told the row is untrusted
  ([forms-and-chat.md](forms-and-chat.md)).
- **Web widgets.** `/pods/{pod_id}/web-widgets` (a chat; public key,
  encrypted signing secret shown once and rotatable, allowed origins, answer,
  looked-after-by). Public endpoints under `/public/web/{public_key}`: session,
  challenge, messages, stream, history, email code and verify, table and rows,
  served only while `PUBLIC_WEB_ENABLED` is on. A session (`visitor_sessions`)
  is opened with nothing (after an Altcha proof when bot protection is on), a
  host token (HS256, `aud` = public key, ≤ 10 minutes) or the secret the page
  kept, and answers with a 15-minute `visitor-access` token sent as
  `Authorization: Bearer`; bodies are JSON. Anonymous sessions end after 90
  days, a contact's 30 days after last use, and a host session never refreshes
  without a fresh host token; reissuing the secret or forgetting the contact
  revokes them. CORS for these paths comes from the widget's own origins, never
  with credentials. An anonymous visitor is an outsider; a host token
  (`host`) or a code (`code`) makes them a contact under a new secret,
  upgrading their conversation in place. A row goes to a table opened to visitors
  (`datastore_public_rows`), as the member who opened it, with the visitor's
  contact on a contact-owned table. `/public/web/widget.js` is the chat and the
  form drawer. Limits per widget,
  session, address and email, failing closed.
- **Outreach policy** is `contacts`' (`contacts.contracts.outreach`), and every
  line to a contact's place -- a member's, an agent's answer, an
  acknowledgement -- is held to it before it goes, in this order: **opt-out
  first** (a `contact_suppressions` row for the handle's sha256: `stop`,
  `unsubscribe`, `bounce`; it outlives the contact being forgotten, and holds
  with nobody looking after contacts), then a handle of the place's kind, then
  the platform's window (WhatsApp: 24 hours from the contact's last message to
  that number, the place's `last_inbound_at`), then, for a member's line,
  `CONTACTS_MEMBER_LINES_PER_CONTACT_PER_DAY`, counted once per line in Redis
  (Redis away is retried, never sent uncounted). A whole message "STOP" (or an
  email whose subject is "unsubscribe") opts the handle out, even from a
  machine; "START" or any message the person typed -- a vouched-for sender, no
  automatic-mail header -- opts it back in. Email to a contact ends with a
  signed unsubscribe link and carries `List-Unsubscribe` (the link and a
  `mailto:` the bot receives) with `List-Unsubscribe-Post: List-Unsubscribe=One-Click`
  (RFC 8058), whose `POST` the unsubscribe controller takes. Handles are
  normalised: phones E.164 (`phonenumbers`, in `CONTACTS_DEFAULT_PHONE_REGION`
  when one is set, else read as international), email trimmed and lower-cased,
  host identities `host:{issuer}:{sub}` with the issuer a widget declares
  (`host_issuer`, `default` unless given at creation).
- **Writing first.** `POST /pods/{pod_id}/contacts/{id}/outreach` `{handle, text}`
  (`contact.message`, editors and up; held to the outreach policy above; an
  `Idempotency-Key` or `client_message_id` makes a retry the same line) posts
  the member's line in the contact's channel at the place that handle is
  reached through, and sends it as a delivery like any line a member writes.
  Email any time: the pod's mailbox's place for that address, opened the first
  time, where the send starts a new thread (`cold_email_thread`) whose reply,
  authenticated and matched to the contact by its email identity, comes back to
  the same place; it ends with an unsubscribe line and a signed link
  (`/public/contacts/unsubscribe`, a confirmation page whose button does it).
  WhatsApp only within 24 hours of their last message to that number, and
  Telegram only where they have written to the bot. A web chat any time while
  the person can still come back to it -- a `web` handle at its own session's
  place, a `host` handle at their latest live session on a widget for the
  product that named them -- with no window: there is nobody to hand the line
  to, so it goes into their conversation and its delivery is `SENT` there, and
  they read it in `/history` on their next visit (a page that keeps the
  session's secret comes back to the same chat). A session that has ended is
  never opened again, so a web handle without a live one posts nothing. Slack
  and Teams handles are answered where the person writes, never written to
  first. A handle that is not theirs, opted out, is outside its window or was
  never written from posts nothing and says why. A bot for known contacts gives a
  stranger one short refusal a day.
- **Unverified email** is parked as a line in the own channel of the member who
  looks after contacts, once an hour per sender and at most
  `SURFACE_PARKED_MAIL_NOTES_PER_SURFACE_PER_HOUR` notes a bot, then one
  summary. Mail a machine sent (`Auto-Submitted`, `Precedence`, list headers,
  mailer-daemon and no-reply senders, our own addresses) is neither answered
  nor parked.
- **Cost.** Runs for contacts and group outsiders are recorded as `contact_run`
  and `outsider_run`, skip the member's personal windows, and count towards a
  `contacts_month` window held to `usage_contacts_caps`
  (`GET /usage/organizations/{id}/contacts-cap` for owners and editors, `PUT`
  for owners). No row is the deployment default
  (`USAGE_CONTACTS_MONTHLY_DEFAULT_USD`, $50, and `USAGE_CONTACTS_MAX_DEFAULT`,
  500 contacts answered); removing the cap is recorded as no limit. Past the cap a surface starts no run: the person is told once a day
  that a person will reply, the member is told, and the conversation is handed
  to them for the rest of the month. The web chat is held the same way. Vision,
  titles and history compaction in an outside run count towards the cap, never
  the member's windows.
- **The app.** A Contacts place in the space (People with a sheet per
  contact; On your website; What contacts can use), a "Private messages from
  people outside" select in a bot's settings, and the contacts cap in the
  organization's Usage -- all behind the `contacts` feature flag.
- **SDKs.** `pod.contacts`, `pod.web_widgets` and
  `pod.functions.set_contacts_invoke` in Python; `client.contacts` (with
  `.widgets`) and `functions.setContactsInvoke` in TypeScript.

- **Switched on deliberately.** `PUBLIC_WEB_ENABLED` is off by default: every
  `/public/web` endpoint and hosted page answers 404, and no widget, open table
  or bot can be set to answer people outside until an operator turns it on.
  `PUBLIC_PAGES_URL` serves the hosted pages on their own cookieless origin.
  New chats and code requests take an Altcha proof-of-work by default
  (`PUBLIC_WEB_ALTCHA_ENABLED`), with no key to provision.
- **Every public endpoint is under `/public/`.** The rule, its exceptions and
  the gate that holds it are in CONTRIBUTING.md.

Not built yet: WhatsApp templates (writing first past the 24-hour window), newsletters to subscribers,
step-up codes for contact functions (the `requires` strength), pod bundles
carrying `contact_owned`, `contact_columns`, `contacts_invoke` and
`include_outside_rows`, and a hand-back control in the app for a conversation
handed to a member.

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
5. **Forms.** A table opened to visitors and any page that adds its rows; see
   [forms-and-chat.md](forms-and-chat.md).
6. **Web chat.** Embedded with a host token, public with anonymous sessions that
   upgrade.
7. **Outbound and consent.** WhatsApp templates, email unsubscribe, and
   newsletters from blog subscriptions.

Each step ships on its own. Step 2 is a working FAQ desk, and step 5 is the
first thing on the web.
