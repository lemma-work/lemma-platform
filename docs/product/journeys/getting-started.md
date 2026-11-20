# Getting started

**Journey:** A person arrives with nothing, and ends up somewhere they can work
with the people they work with.

Everything in Lemma happens inside an organization, and almost everything
happens inside a pod inside an organization. This journey covers getting to that
first organization — by making one, by being invited to one, or by finding one
that already exists. It stops at the point where a pod is worth making, which is
[Building a pod](building-a-pod.md).

Every way in — the web app, a plugin's install page, a WhatsApp or Telegram
sign-up, a company's Slack or Teams installation — puts a person in their first
organization by one rule. Invitations waiting for them come first, then an
organization they already belong to, then their company's organization when it
is open to their verified work domain; otherwise one is made, with a Lemma
everyone in it shares, and they land in that Lemma. A company Slack or Teams
installation fixes the organization: signup cannot grant access to another
organization or bypass the company's membership policy.

Onboarding keeps its primary Continue, Create, or Join action visible within
the window, including at the desktop's minimum size and with enlarged text.
Long setup content scrolls independently of that action. Connection failures
are shown with a retry action rather than an indefinite loading message.

---

## Capability: Sign up and sign in

### PS-ONB-001 — A new person signs up and becomes a known user
**Status:** covered

- When a person signs up with an email address and a password, the system shall
  create a user for that email and sign them in.
- When a person signs up, the system shall record `auth.signed_up` with the
  method they used.
- The system shall treat email addresses case-insensitively, so that a person
  who signs up as `Ada@example.com` signs in as `ada@example.com`.
- If a person signs up with an email that already has a user, then the system
  shall refuse and shall not create a second user for that email.
- If a person signs up with an email that already has a user through a different
  sign-in method, then the system shall say which method that email already
  uses rather than failing generically.
- A person may continue with an email code on the web. Verifying the code shall
  reuse their existing account, including accounts created through chat, without
  changing their identity or duplicating their workspaces.
- Email codes shall expire within ten minutes, allow three incorrect attempts,
  and become unusable after completion or replacement. Resending requires a
  sixty-second wait. Before verification, the response shall not reveal whether
  an account exists.
- A successful email-code login shall preserve the requested desktop, CLI, or
  import destination through the same redirect checks as other login methods.

**Contracts:** `auth.signed_up`

### PS-ONB-002 — A person who has joined nothing sees an empty start, not an error
**Status:** covered

- When a person who belongs to no organization asks for their organizations,
  the system shall return an empty list.
- When a person who belongs to no organization asks for their navigation, the
  system shall return an empty navigation rather than an error.
- The system shall let a person read their own profile before they belong to any
  organization.

**Contracts:** `org.list`, `org.navigation`, `user.current.get`, `user.profile.get`

### PS-ONB-003 — A signed-in person is identified consistently everywhere
**Status:** covered

- While a person holds a valid session, the system shall resolve that session to
  the same user across every API, the CLI, and both SDKs.
- If a token is expired, malformed, or signed by an unknown key, then the system
  shall refuse the request and shall not fall back to an anonymous identity.

**Contracts:** `auth.verify_token`, `user.current.get`

### PS-ONB-004 — A person sets a display name and preferences that follow them
**Status:** covered

- When a person updates their profile, the system shall apply it to every
  organization they belong to, because a person has one profile and not one per
  organization.
- Where a person has set no display name, the system shall fall back to
  something stable and human rather than showing an identifier.

**Contracts:** `user.profile.upsert`, `user.profile.get`

---

### PS-ONB-005 — A person chooses comfortable chat text on their device
**Status:** manual

> **Verified by:** opening Settings > Appearance in the browser, selecting each
> chat text size, inspecting messages and the composer, and reloading to confirm
> the choice persists. The API scenario suite cannot inspect rendered typography.

- Appearance shall offer Small, Default, and Large chat text with a live preview.
- The default message size shall be 15px; Small shall be 14px and Large 17px.
- The choice shall apply immediately to conversation prose and the composer,
  without changing navigation or document typography.
- Touch-device composers shall remain at least 16px to avoid focus zoom.
- The choice shall persist in the current browser and restore before paint.
  Missing, invalid, or unavailable storage shall use the default.

---

### PS-ONB-006 — Authentication resumes the person's requested destination
**Status:** manual

> **Verified by:** opening a workspace or app link while signed out, switching
> to sign-up, completing email verification, and continuing to the original
> destination. Repeat with password reset and provider sign-in. Browser
> regressions use `npm run test:auth-browser` in `lemma-frontend`; real provider
> consent and email delivery require a configured deployment.

- Sign-in and sign-up shall preserve the requested path, query, and fragment.
- Switching auth screens or completing a provider round trip shall retain the
  destination. Completing a flow shall clear its saved destination.
- When verification is required, the portal shall send a verification email
  and let the person resend or retry without creating another account.
- Sign-in and sign-up shall offer email code by default, with password and
  provider options available. A verified code shall resume the requested
  destination without an additional email-verification step.
- A waiting tab shall recognize verification completed in another tab and let
  the person continue to its saved destination. A separate browser without a
  saved destination shall use the workspace default after sign-in.
- An authenticated visitor shall continue without entering credentials again.
- Auth routes and untrusted origins shall not be accepted as destinations.
  A refused destination shall not revive a previously saved destination.

### PS-ONB-007 — A new person confirms their name and can prove their phone before they start
**Status:** manual

> **Verified by:** signing up with an email code on a deployment where WhatsApp
> mobile verification is enabled, confirming the step opens over the app with
> the name fields and a scannable code, sending the message from a phone, and
> reloading to confirm the step does not return. The API scenario suite cannot
> inspect the rendered dialog.

- When a person lands in the app for the first time and their account has no
  first name, or has no mobile number where the deployment can verify one over
  WhatsApp, the system shall ask for what is missing in one step before they
  start.
- The step shall offer the name the account already holds, so a name supplied by
  a sign-in provider is confirmed rather than retyped.
- The phone shall be proved by sending one message from it, by scanning a code
  or opening WhatsApp, and never by typing a number the system then trusts.
- The step shall not imply that a teammate can be reached on WhatsApp yet. At
  that point none has been connected there, so proving the phone only means a
  teammate connected later recognises the person.
- The phone shall be optional, and the whole step shall be skippable.
- The system shall not ask an account that already has both, an account older
  than its first week, or a person who has already continued or skipped in that
  browser.
- Where the way in already asked for the name (PS-ONB-009), the step shall not
  ask it again; it opens only for a phone still to prove.

### PS-ONB-008 — A person is asked what they do once, and the answer stays with them
**Status:** covered

- When a person starts for the first time, the system shall ask what they do,
  offering sales, marketing, product, founder, operations, HR, project
  management, engineering and support, any number of them, and a few words of
  their own.
- The system shall keep the answer on the person, not on a browser or an
  organization, so every device and every organization they work in reads the
  same answer.
- Skipping the question shall be an answer too: the system shall not ask a
  person who has answered or skipped again.
- Answering shall change nothing else the person has set.
- The system shall show the answer to the person alone: not in an
  organization's or a Lemma's list of people, and not to an app.
- If an answer names a job the system does not offer, or its own words run past
  120 characters, then the system shall refuse it and keep the answer it had.

**Contracts:** `users.work.set`, `user.current.get`

### PS-ONB-009 — Every way in looks the same: your Lemma asks, in its own voice
**Status:** manual

> **Verified by:** on a stack running this build, signing up on the web with a
> work address and adding an app through to opening it; signing up with a
> personal address, from Home and from a link to a Lemma they are not in; a
> colleague from the same domain; a person invited to a Lemma; and accepting
> an invitation to an organization from Home -- at desktop and phone width, in
> light and dark. `npm test` in `lemma-frontend` checks the steps each door
> walks, the speaker's status, the setup stages, the apps offered and added,
> the accounts asked for and the workspace's name
> (`tests/arrival-flow.test.ts`), and the words they use
> (`tests/outcomes-copy.test.ts`).

- Whichever way a person arrives — the web app, an invitation — the system
  shall show the same screens: on one side whoever is asking, big, with what
  it is doing in a word and how far along they are; on the other the
  question, in the asker's own words. Lemma's bars ask until their Lemma
  exists, listening as they type; from then on their Lemma asks, by its
  living face and name, and everything they choose attaches to it there:
  empty spots for apps when it is made, the apps they tick (each flying over
  from the list), those apps going in and in, and the places it can be
  reached.
- The order shall be: their name and time zone; meeting their Lemma; what
  they do and the apps that fit, on one screen; where to reach it, while the
  apps go in; ready.
  Their name and time zone shall be asked once, and not again in a dialog over
  the Lemma they land in.
- Meeting their Lemma, the system shall say, by name, that it is making them a
  Lemma, what a Lemma is, what it is called and who is in it: for a team,
  everyone in the organization; for one person, that it is theirs alone and
  that working with someone is another Lemma they start and invite them to.
  Its creator may rename a team's Lemma there.
- The system shall decide nothing a person could decide better: where their
  company's organization is open to them, it shall offer it and let them start
  their own instead; otherwise it shall make or join without asking, while
  they type their name. A workspace of one the system could not name shall
  take that name ("Sam’s workspace").
- Somebody who came into an organization that has Lemmas already shall be
  greeted by the one they came in by, and offered the others to join.
- After what they do, the system shall offer a few apps picked for the
  answer, each with what it does and what it reads, and shall say which Lemma
  they go into. An app whose install page would ask a question shall stop
  before changing anything and link to that page.
- While the apps go in, the system shall offer the places the Lemma can be
  written to from: the email address it already has, and WhatsApp, Telegram
  and Slack where this deployment lets one tap turn them on. It shall leave
  out a place nobody can use here. When the person writes to it from one of
  them, the system shall say it heard them, there and on the Lemma's face.
  With no place to offer, the step shall be the apps going in, in plain
  stages (places for its information, helpers, the app, what it does on its
  own).
- An app that stops shall say so, and Try again shall carry on from where it
  stopped.
- Ready, the system shall show the whole Lemma — its apps, where it can be
  reached, who is in it — the accounts its apps read to connect, a few first
  things to ask it, and the way into its first app.
- A way in already under way shall not be taken over by the link it began
  on: the Lemma an address named is settled when the way in starts.
- Somebody who came in another way (a chat app, an install page) and was not
  asked what they do shall be asked when they next come to Home, never in place
  of a chat, an app or a Lemma a link opened.

**Contracts:** `users.ensure_first_workspace`, `users.work.set`

---

## Capability: Create an organization

### PS-ONB-010 — The person who creates an organization owns it
**Status:** covered

- When a person creates an organization, the system shall make them a member of
  it with the owner role.
- When an organization is created, the system shall record
  `organization.created`.
- The system shall let a person belong to more than one organization.

**Contracts:** `org.create`, `organization.created`

### PS-ONB-011 — An organization has a handle that survives being renamed
**Status:** covered

- When an organization is created without a handle, the system shall derive one
  from its name.
- When an organization is renamed, the system shall keep its existing handle, so
  that links and references to it continue to resolve.
- If a person asks for a handle that is already taken, then the system shall
  refuse and shall say the handle is taken.

**Contracts:** `org.create`, `org.update`, `org.slug_availability`

### PS-ONB-014 — Two organizations may share a display name
**Status:** covered

- The system shall allow two unrelated organizations to carry the same display
  name, because names are how people recognise their own organization and not
  how the system tells organizations apart.
- If a person asks whether a name is in use, then the system shall not reveal
  whether some other organization on the deployment is using it.

**Contracts:** `org.create`, `org.update`, `org.slug_availability`

### PS-ONB-013 — Only an owner changes what the organization is
**Status:** covered

- When an owner renames the organization or changes how people may join it, the
  system shall apply the change.
- If a member who is not an owner attempts either, then the system shall refuse.

**Contracts:** `org.update`, `org.get`

### PS-ONB-015 — A new organization opens in its first Lemma: shared by a team, its person's own alone
**Status:** covered

> **Verified by:** the scenario for the organization a first workspace makes.
> The organization menu is checked in the app, on a stack running this build:
> making a second organization from it opens in its own Lemma the same way, and
> that Lemma's header says everyone in the organization can find it.

- When the system makes an organization for a person who belongs to none — on
  the web, from a plugin's install page, or in a chat sign-up — or a person
  makes one from the organization menu, the system shall also make a Lemma in
  it and land them there.
- A company's organization shall get a Lemma called what the organization is
  called, open to every member of the organization, and everyone who joins the
  organization afterwards shall be in it (PS-POD-024), from the moment they
  arrive.
- A workspace of one shall get a Lemma called Personal that is its person's
  alone: nobody invited into the workspace later shall be put in it, and it
  shall not be marked as one everyone shares.
- The person who made the organization shall own it and administer its Lemma,
  with everything an owner may do from the first moment.
- If that Lemma cannot be made, then the system shall still open the
  organization, on its list of Lemmas, where one can be made.
- Asking again for a first workspace shall make nothing new: not a second
  organization, and not a second Lemma beside the first.

**Contracts:** `users.ensure_first_workspace`, `org.create`, `pod.create`, `pod.get`

### PS-ONB-016 — An organization's own Lemma starts by bringing the team's stuff in
**Status:** manual

> **Verified by:** opening the Lemma PS-ONB-015 made, before anything is in
> it: the welcome asks where the team's stuff is, picking places counts them
> on the button, and Bring in opens a chat with the Lemma whose first message
> names those places. "Start another way" shows the ways in PS-POD-061 offers.
> `npm test` in `lemma-frontend` checks the request's wording and what counts
> as empty (`tests/lemma-space.test.ts`).

- While the organization's own Lemma holds nothing a person put there, its home
  shall say in one sentence what a Lemma holds, then ask where the team's stuff
  is today, offering Notion, Google Drive, Airtable, Google Sheets and Slack,
  each saying which of the Lemma's tabs it lands in.
- When the person picks places and chooses Bring in, the system shall start a
  chat with the Lemma whose first message names those places, and the Lemma
  shall bring their contents into its pages, tables and files.
- Before bringing in anything only that person could see at its source, the
  Lemma shall ask, because everyone in the Lemma can read what lands in it.
- The person shall be able to start another way instead.

**Contracts:** `pod.get`, `agent.conversation.create`

---

## Capability: Bring a team in

### PS-ONB-020 — An invited person joins with the role they were offered
**Status:** covered

- When an owner or editor invites an email address with a role, the system shall
  create a pending invitation for that email and notify it.
- When the invited person accepts, the system shall make them a member with
  exactly the role the invitation offered.
- If an inviter attempts to offer a role carrying permissions they do not hold
  themselves, then the system shall refuse. An editor may therefore invite an
  editor or a member, and may not invite an owner.
- When someone joins an organization, the system shall record
  `organization.member_joined`.
- If a person attempts to accept an invitation addressed to a different email,
  then the system shall refuse.

**Contracts:** `org.invitation.invite`, `org.invitation.accept`, `organization.member_joined`

### PS-ONB-021 — An invitation can carry a pod, and accepting it grants both
**Status:** covered

- Where an invitation names a pod, accepting it shall make the person both a
  member of the organization and a member of that pod, with the pod role the
  invitation offered.
- If an inviter attempts to offer a pod role they could not confer by adding the
  person to that pod themselves — because they do not manage that pod's members,
  or the role carries permissions they do not hold there — then the system shall
  refuse. An invitation is a pod grant that lands later, not a way around one.
- If the pod named by an invitation cannot be granted — because it was deleted
  after the invitation was sent, for example — then the system shall refuse the
  acceptance and shall say which pod it could not grant, leaving the invitation
  usable once the problem is fixed.

**Contracts:** `org.invitation.invite`, `org.invitation.accept`, `pod.member.add`

### PS-ONB-022 — An invitation stops working when it should
**Status:** covered

- While an invitation is pending and unexpired, the system shall allow the
  addressed person to accept it.
- When an invitation passes its expiry, the system shall treat it as expired and
  shall refuse to accept it.
- When an owner or editor revokes a pending invitation, the system shall refuse
  any later attempt to accept it.
- If a person attempts to accept an invitation they have already accepted, then
  the system shall refuse rather than granting membership twice.

**Contracts:** `org.invitation.accept`, `org.invitation.revoke`, `org.invitation.get`

### PS-ONB-023 — Inviting someone already inside is refused clearly
**Status:** covered

- If an owner or editor invites an email that already belongs to a member of the
  organization, then the system shall refuse and shall say they are already a
  member.
- If an owner or editor invites an email that already has a pending invitation
  to the same organization, then the system shall refuse rather than creating a
  second one.

**Contracts:** `org.invitation.invite`, `org.invitation.list`

### PS-ONB-024 — A person can see the invitations waiting for them
**Status:** covered

- When a person whose email address is verified asks for their invitations,
  the system shall list every pending invitation addressed to that email across
  all organizations.
- When a person whose email address is not verified asks for their invitations,
  the system shall list none, and the invitation's own link shall remain the way
  to accept it. Listing by an unproven address would let whoever signed up as it
  first take the seat.
- The system shall show enough on each invitation — the organization, the role,
  and the pod when it names one — for a person to decide without accepting it
  first.

**Contracts:** `org.invitation.list_mine`, `org.invitation.get`

> **Verified by:** the listing for a verified address is proven by the backend's
> own e2e suite; the scenario suite boots with verification off and proves the
> unverified half. A lane with verification on would let a scenario prove both.

---

### PS-ONB-025 — An invited person sees the Lemmas they can join
**Status:** manual

> **Verified by:** on a stack running this build, signing up as somebody
> invited to one Lemma, and accepting an invitation to an organization alone
> from Home while working in another: the Lemma they were invited to leads,
> then the organization's own Lemma with them already in it, then the Lemmas
> open to everyone there with Join, then "Start a new one"; continuing from
> Home lands in the organization they joined. The backend's e2e suite proves
> they are in the organization's Lemma in the answer to accepting
> (`test_first_workspace_api_e2e.py`).

- When a person accepts an invitation, at sign-up or from Home, the system shall
  show them that organization's Lemmas: the one they were invited to first,
  then the ones they are in, then the ones open to everyone there, each with a
  way to join it, and a way to start a new one.
- A person new to the organization shall be in the Lemma everyone in it shares
  from the moment they accept.
- When they continue, the system shall open the organization they joined, on
  the Lemma they were invited to when there was one.

**Contracts:** `org.invitation.accept`, `pod.list_joinable`, `pod.join`

---

## Capability: Join an organization that already exists

### PS-ONB-030 — A person is offered the organizations they could join
**Status:** covered

- When a person with a work email asks for suggestions, the system shall list
  organizations that allow self-joining and match their email domain.
- The system shall not suggest organizations the person already belongs to.
- Where a person's email is from a consumer email provider, the system shall
  suggest nothing rather than matching on the provider's domain.

**Contracts:** `org.suggested`

### PS-ONB-031 — A person joins an organization that is open to them
**Status:** covered

- When a person joins an organization that is open to everyone, the system shall
  make them a member with the least-privileged role.
- When a person whose email domain matches joins a domain-restricted
  organization, the system shall make them a member with the least-privileged
  role.
- If a person attempts to join an invite-only organization, then the system
  shall refuse.
- If a person attempts to join a domain-restricted organization from a
  non-matching email domain, then the system shall refuse.
- When a person who is already a member attempts to join again, the system shall
  leave their existing membership and role untouched.

**Contracts:** `org.join_auto_join`, `organization.member_joined`

### PS-ONB-032 — Colleagues from one work domain land in one organization
**Status:** planned

> **Verified by:** the backend's own e2e suite
> (`test_first_workspace_e2e.py`, `test_first_workspace_api_e2e.py`). The
> scenario suite boots with email verification off, and an unproven address
> claims no domain; a lane with verification on would let a scenario prove it.

- When the first person from a work domain signs up with a verified address and
  belongs nowhere, the system shall make their company's organization, named
  after the company, and shall leave it open to verified addresses from that
  domain unless its owner closes it.
- When a colleague from the same domain then signs up, the system shall put
  them in that organization and in its shared Lemma, rather than making them an
  organization of their own.
- Where a person would rather not join their company's organization, the system
  shall let them start one of their own instead, open by invitation only.
- If another organization already holds the domain, then the system shall still
  make the new organization, open by invitation only, rather than refuse the
  sign-up.
- An address on a public mail provider, or one not yet verified, shall claim no
  domain and shall join no organization by its email domain. An invitation it
  accepts still brings it in.

**Contracts:** `users.ensure_first_workspace`, `org.suggested`

---

## Capability: Change and remove membership

### PS-ONB-040 — Whoever manages people changes what a member may do
**Status:** covered

- When an owner or editor changes a member's role, the system shall apply it
  immediately to every later request that member makes.
- If a member who is neither owner nor editor attempts to change any role, then
  the system shall refuse.
- If a person attempts to give anyone a role carrying permissions they do not
  hold themselves — themselves included — then the system shall refuse. An
  editor may therefore make an editor or a member, and may not make an owner.
  This holds through every path that grants a role, including those that grant
  one as a side effect, such as approving a request to join.
- If an editor attempts to change the role of an owner, then the system shall
  refuse: nobody may take away authority they do not hold either.

**Contracts:** `org.member.update_role`, `org.member.list`

### PS-ONB-041 — An organization always has at least one owner
**Status:** covered

- The system shall ensure every organization has at least one owner at all
  times.
- If removing a member would leave the organization with no owner, then the
  system shall refuse and shall say another owner must be appointed first.
- If changing a member's role would leave the organization with no owner, then
  the system shall refuse on the same grounds.
- The system shall apply both rules to a person acting on their own membership,
  because leaving is the most common way to reach the state.

**Contracts:** `org.member.remove`, `org.member.update_role`

### PS-ONB-042 — Removal respects the role hierarchy
**Status:** covered

- When an owner removes any member, the system shall remove them.
- When an editor removes a member who is not an owner, the system shall remove
  them. An editor may remove another editor.
- If an editor attempts to remove an owner, then the system shall refuse.
- If a member who is neither owner nor editor attempts to remove anyone other
  than themselves, then the system shall refuse.

**Contracts:** `org.member.remove`, `org.member.list`

### PS-ONB-043 — A person can leave on their own
**Status:** covered

- When a person removes their own membership, the system shall remove it without
  requiring a role, subject to PS-ONB-041.
- When a person leaves an organization, the system shall stop showing them its
  pods and content on their next request.

**Contracts:** `org.member.remove`, `org.navigation`

---

## Capability: Delete your account

### PS-ONB-080 — A person deletes their own account, from the app or the website
**Status:** planned

- The system shall let a signed-in person delete their own account from their
  account settings, on the web and on the phone, and from a public page that
  works without the app once they sign in.
- When a person asks to delete their account, the system shall say plainly
  what deleting it removes and what it keeps.
- When a person asks to delete their account, the system shall ask them to
  type the account's email address to confirm.
- If the typed address is not the account's, then the system shall delete
  nothing.
- When the account is deleted, the system shall end every session the person
  has, on every device, and every connection a client or computer holds on
  their behalf.
- When the account is deleted, the system shall let the same email address
  sign up again as a new person, with nothing of the old account.

**Contracts:** `user.current.delete`

### PS-ONB-081 — Deleting an account unlinks the person from work they shared
**Status:** planned

- When a person deletes their account, the system shall erase what it held
  about them: their name, email address, phone number, time zone and the chat
  accounts they proved.
- When a person deletes their account, the system shall take them out of every
  organization and pod, and disconnect the accounts they connected there.
- When a person deletes their account, the system shall keep what they shared
  with others — messages, records, files, pods — in the organization it
  belongs to, shown as by "Deleted user".
- When a person deletes their account, the system shall delete every
  organization nobody else is in, with its pods.
- If a deletion fails before the account is deleted, then the system shall
  leave the account, its memberships and its data exactly as they were.
- When an account has been deleted, the system shall retry what follows from
  the deletion — ending sessions, disconnecting accounts, letting go of paired
  computers and proved chat accounts — until it is done.
- If what follows a deletion still cannot be finished, then the system shall
  keep it visible to the operator.
- If what follows a deletion still cannot be finished, then the system shall
  let the operator run it again.

**Contracts:** `user.current.delete`

### PS-ONB-082 — An account is not deleted out from under what depends on it
**Status:** planned

- If the person is the only owner of an organization other people are in, then
  the system shall refuse to delete the account, name that organization, and
  ask them to make someone else an owner first (PS-ONB-041).
- If the person pays for something the deletion would leave running, then the
  system shall refuse to delete the account and say what to cancel.
- When the system refuses, it shall change nothing.

**Contracts:** `user.current.delete`

---

## Not covered here

| Concern | Where it lives |
|---|---|
| Creating the first pod, pod membership, pod roles | [Building a pod](building-a-pod.md) |
| What a member may do to a specific resource | [Sharing and permissions](sharing-and-permissions.md) |
| Email deliverability, verification, abuse protection | [Authentication hardening](../../authentication-hardening.md) |
| Usage limits that apply to an organization | [Operating a deployment](operating-a-deployment.md) |


## Capability: Prepare the first conversation

### PS-ONB-050 — First-chat setup lands the person in their organization's Lemma
**Status:** covered

- First-chat setup — a WhatsApp or Telegram sign-up, or the web's first
  workspace — shall follow PS-ONB-015 and PS-ONB-032, and land the person in the
  Lemma their organization shares, with its assistant ready; a chat sign-up
  shall make that Lemma's assistant the one their chat reaches.
- Where an invitation brought them to a particular pod, they shall land there
  instead, and still be told which Lemma the organization shares.
- Only in an organization with no shared Lemma, or one whose shared Lemma the
  person left, and only when asked, shall setup make a private pod owned solely
  by the person, so the chat has somewhere to go. Asking again shall not put
  back somebody who left the shared Lemma.
- Retrying or concurrently requesting setup shall return the same workspace
  without creating duplicates.
- Only a verified email domain may select or claim an email-domain organization.

**Contracts:** `users.ensure_first_workspace`

## Capability: Find your way around

### PS-ONB-070 — A new person is shown what Lemma is and where everything is
**Status:** manual

> **Verified by:** signing up on a live deployment, hiring the first teammate
> and opening its Home: the tour opens once, after the first-profile step, and
> not again on reload. Then taking it again from the help button and with
> `?tour=1`, at desktop and phone widths. `npm test` in `lemma-frontend` checks
> the stops and when the tour is offered (`tests/app-tour.test.ts`); the overlay
> itself needs a browser.

- When a person whose account is less than a week old first opens a teammate's
  Home in a browser, the system shall offer a tour that says what Lemma is and
  where everything is.
- The tour shall point at the real controls: the teammates, the places in a
  teammate's space, the box to ask in, where requests for the person's answer
  or approval arrive, sharing, and the teammate's own page.
- The system shall offer the tour once per person per browser, whether it was
  finished or dismissed.
- While another dialog is open, the system shall wait for it to close before
  offering the tour.
- If the person is in a conversation, then the system shall not interrupt it to
  offer the tour.
- The system shall let a person end the tour at any stop, and take it again at
  any time from the help menu.
- While the tour is showing, the system shall not act on clicks or shortcuts
  meant for the workspace beneath it.

### PS-ONB-071 — A person can ask how the place they are in works
**Status:** manual

> **Verified by:** opening the help menu on Pages, a page, Workflows, a
> workflow run, Tables, a table and Apps, and from the empty Workflows and
> Pages, at desktop and phone widths. `npm test` in `lemma-frontend` checks
> each guide's lines and which place gets which guide
> (`tests/place-guides.test.ts`); the card itself needs a browser.

- Where a place has things to do that nothing on screen advertises, the
  system shall offer a short guide to it from the help menu, as the menu's
  first item while the person is in that place.
- When a place is empty, the system shall link to its guide beside the ways
  to make the first thing.
- The guide shall say each thing as a gesture and what it does, by the name of
  the teammate that does the work.
- Where a place can be learned by trying, the guide shall offer it: a real
  page that explains pages, or a request put in the chat box for the person to
  send.
- The system shall not open a guide on its own.
- If the person goes to another place, then the system shall close the guide.

## Capability: Explore the public website

### PS-ONB-060 — A visitor can learn about Lemma without signing in
**Status:** covered

- When a visitor opens documentation, company information, legal terms, the blog,
  changelog, templates, or downloads, the system shall show the public content
  without requiring a session.
- When a visitor follows an indexed documentation link, the system shall show
  the named guide; an unknown guide shall return a not-found response.
- The system shall provide navigation between public pages and the workspace.

### PS-ONB-061 — An AI reader can discover and read the public website
**Status:** covered

- When a reader requests Markdown for a supported public page, the system shall
  return the page's content as Markdown at the same address and distinguish the
  representation in its cache headers.
- The system shall provide an AI index, a public API specification, a sitemap,
  crawler rules, and a feed for dated content.

### PS-ONB-062 — Existing public and workspace entry links keep working
**Status:** covered

- When a person follows an existing sign-in, legal, conversation, table, or
  account-billing link, the system shall route them to the corresponding view
  in the main frontend while retaining its resource identity.
- When a person opens a shared contact, the system shall offer a contact file
  with the channels carried by the link.
- When a social crawler requests a public preview image, the system shall
  return an image suitable for a link preview.
