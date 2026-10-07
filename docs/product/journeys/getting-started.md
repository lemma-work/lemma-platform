# Getting started

**Journey:** A person arrives with nothing, and ends up somewhere they can work
with the people they work with.

Everything in Lemma happens inside an organization, and almost everything
happens inside a pod inside an organization. This journey covers getting to that
first organization — by making one, by being invited to one, or by finding one
that already exists. It stops at the point where a pod is worth making, which is
[Building a pod](building-a-pod.md).

After identity verification, first-chat setup selects an eligible organization
and ensures a private personal pod with an assistant. Web and shared Lemma bots
reuse an eligible workspace before creating one. A company Slack or Teams
installation fixes the organization: signup cannot grant access to another
organization or bypass the company's membership policy. Importing a pod may
prepare only the organization, without creating a spare personal pod.

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

## Not covered here

| Concern | Where it lives |
|---|---|
| Creating the first pod, pod membership, pod roles | [Building a pod](building-a-pod.md) |
| What a member may do to a specific resource | [Sharing and permissions](sharing-and-permissions.md) |
| Email deliverability, verification, abuse protection | [Authentication hardening](../../authentication-hardening.md) |
| Usage limits that apply to an organization | [Operating a deployment](operating-a-deployment.md) |


## Capability: Prepare the first conversation

### PS-ONB-050 — First-chat setup yields one usable personal workspace
**Status:** covered

- First-chat setup shall reuse an eligible organization and ensure a private pod
  owned solely by the person, with its assistant ready. Retrying or concurrently
  requesting setup shall return the same workspace without creating duplicates.
- Only a verified email domain may select or claim an email-domain organization.
- Organization-only setup for an importer shall create no spare personal pod;
  a later first-chat request shall still ensure a pod and assistant.

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

## Capability: Hire a teammate for a role, and agree how it is judged

### PS-ONB-080 — Hiring a role brings what the role card lists
**Status:** planned

- When a person hires a role that comes with a template, the system shall
  give the new teammate the template's skills, tables and scorecard before
  the teammate is shown to them, without asking anything further.
- If part of the template does not arrive, the system shall still hire the
  teammate and say what did not arrive, rather than returning the person to
  the hire button.
- The system shall not connect an account, start a schedule or send anything
  as part of hiring; standing work a role offers is started only when a
  person turns it on.
- A first task that needs a connector shall say which one and offer to
  connect it, and shall never imply it is already connected.
- The roles on offer shall be the templates the server ships, each with a
  card read from the template itself, so a card never lists a skill or a
  measure the hire will not get, and a new role needs no change to the app.

### PS-ONB-081 — A person chooses how a teammate is judged
**Status:** planned

- The system shall judge a teammate on the work it makes: each measure is a
  test over the teammate's own rows of work, so two teammates with different
  jobs are not shown the same measures.
- The system shall show each measure with where it is counted from and its
  last four weeks as counted, before anyone keeps it, and let a person turn it
  on or off and pick its target.
- A number on a scorecard shall open to the rows it was counted from.
- When a person says what good looks like in their own words, the system
  shall offer a measure only after it has been counted over past weeks, and
  shall add it only when that person keeps it.
- Checks every teammate shares shall be stated once, not listed as measures.
- A measure the system cannot count yet shall be shown off, with the reason,
  and cannot be turned on.
- When a person keeps the measures, the system shall start a weekly review
  of the teammate that runs as that person, and shall show it with the
  teammate's other schedules.
- Until the review is on for a teammate that came with a scorecard, the
  system shall say once, in that teammate's space, that setting up is not
  finished.

### PS-ONB-082 — The weekly review counts in code and changes nothing on its own
**Status:** planned

- Every number in a review shall come from what the system recorded — the
  approvals people gave, the schedules that ran, the questions left
  unanswered, the teammate's tables — and never from the teammate's own
  judgement of its work.
- A review shall not ask anyone a question or wait for an answer.
- A review shall suggest changes — a note to remember, a fix to a skill, a
  schedule — and shall make none of them; a person adds or dismisses each.
- When a person adds a suggestion, the system shall check it before making
  the change, and shall refuse one that would write outside the place it
  names or produce a skill that would not load.

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
