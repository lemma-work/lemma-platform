# Building a pod

**Journey:** A person turns an empty organization into a pod that holds real
work, and decides who is in it.

A pod is the unit of work in Lemma. Tables, documents, functions, workflows,
schedules, agents, surfaces, and apps all live inside one, and every one of them
inherits the pod's answer to "who can touch this". A person's first pod is the
moment the product becomes useful, so this journey is about getting there
quickly and then controlling it precisely.

This journey covers the pod itself and who belongs to it. What goes *inside* a
pod is [Working with data](working-with-data.md),
[Automating work](automating-work.md), and
[Agents and conversations](agents-and-conversations.md). Fine-grained control
over a single resource is [Sharing and permissions](sharing-and-permissions.md).

The workspace shall always offer an Apps view, including when a teammate has no
apps. App ideas shall be grouped by Personal, Marketing, Product, Sales and
Engineering, with four examples visible in each category. Build actions shall
use the current teammate name. Example previews shall be labeled as examples. Choosing an example
shall prepare an editable request in the conversation without sending it.
Existing apps shall remain reachable in their own tabs.

---

## Capability: Create a pod

### PS-POD-001 — A member of an organization creates a pod and administers it
**Status:** covered

- When a member of an organization creates a pod, the system shall create it and
  make that person a pod admin.
- When a pod is created, the system shall record `pod.created`.
- When a pod is created, the system shall prepare its storage so the first table
  or document does not have to wait for it.
- If a person who does not belong to the organization attempts to create a pod
  in it, then the system shall refuse.

**Contracts:** `pod.create`, `pod.created`

### PS-POD-002 — A pod's name identifies it within its organization
**Status:** covered

- The system shall keep pod names unique within an organization, so that a name
  resolves to exactly one pod.
- If a person creates a pod whose name is already used in that organization,
  then the system shall refuse and shall say the name is taken.
- The system shall trim and normalise a pod name on the way in, so that names
  differing only by surrounding whitespace collide rather than both being
  accepted.
- When a pod is deleted, the system shall free its name for reuse.

> Names are addresses here, not labels: `lemma --pod <name>`, bundle references,
> and inbound email routing all resolve a pod by name. Two pods answering to one
> name would make each of those ambiguous. This is the opposite of the rule for
> organization *display* names (`PS-ONB-014`), which are labels and are shared
> freely — the difference is whether anything resolves through them.

**Contracts:** `pod.create`, `pod.update`, `pod.list`

### PS-POD-003 — A pod carries the settings its work depends on
**Status:** covered

- When a pod admin updates part of a pod's settings, the system shall leave the
  parts they did not mention unchanged.
- Where a pod pins a default agent runtime, the system shall apply it to agents
  in that pod that do not pin one themselves.
- Where a pod pins nothing, the system shall fall back to the deployment default
  rather than failing.

**Contracts:** `pod.update`, `pod.get`

### PS-POD-004 — A person makes a pod the way they would make a channel
**Status:** manual

> **Verified by:** on a stack running this build, choosing + beside Lemmas in
> the web app's sidebar: a dialog opens over the page, shows every face to
> pick from, asks for a name, what it is for, who can find it and people, and
> Create opens the new pod on its empty page (PS-POD-061). Opening `/l/new` opens the same dialog over the
> list of pods.

- When a member makes a pod in the web app, the system shall require only its
  name. It shall also offer, and never require: what it is for, who can find
  it (everyone in the organization, or only the people added), and people to
  add.
- The system shall not ask, when a pod is made, for anything the pod should
  own, a target, a measure, or a template. A template is added afterwards,
  from the pod's empty page (PS-POD-061).
- When the pod is made, the system shall open it on its home.
- The system shall give the new pod a face no other pod in the organization
  wears, while one is left, and shall show every face so the person can pick
  another before making it.
- If a person chosen cannot be added, then the system shall still make the pod,
  and shall open who has access so the person can try again.

**Contracts:** `pod.create`, `pod.member.add`

---

## Capability: Put people in a pod

### PS-POD-010 — A pod admin adds an organization member to the pod
**Status:** covered

- When a pod admin adds an organization member to the pod with a role, the
  system shall grant them that role in the pod.
- When an organization owner adds an organization member to a pod, the system
  shall grant them that role whether or not the owner belongs to the pod: an
  owner reaches every pod in their organization.
- If an organization editor who does not manage the pod attempts to add someone
  to it, then the system shall refuse: managing the organization's people is not
  authority inside a pod.
- When someone joins a pod, the system shall record `pod.member_joined`.
- If a pod admin attempts to add someone who is not a member of the pod's
  organization, then the system shall refuse — organization membership is the
  outer boundary and a pod cannot widen it.
- If a pod admin attempts to add someone who is already a member of the pod,
  then the system shall refuse rather than creating a second membership.

**Contracts:** `pod.member.add`, `pod.member.list`, `pod.member_joined`

### PS-POD-011 — A person's pod role decides what they may do inside it
**Status:** covered

- The system shall offer four built-in pod roles, ordered from least to most
  able: viewer, user, editor, admin.
- While a person holds a pod role, the system shall allow exactly the actions
  that role permits and refuse the rest, on every request and not only in the
  interface that offered it.
- When a pod admin changes a person's roles, the system shall apply the change
  on the affected person's very next request, and shall never require a cached
  snapshot to expire first.

> "Next request" is the whole promise, and it is worth saying why it is
> achievable rather than aspirational. The cached role snapshot is dropped
> inside the transaction's commit, and the commit completes before the response
> to the changing request is sent — so by the time an admin is told the change
> landed, no later request can be served from the old snapshot. A request
> already in flight *alongside* the change may still see the old roles; that is
> ordinary concurrency and not what this promises.

**Contracts:** `pod.member.update_roles`, `pod.permissions.me`, `pod.permissions.catalog`

### PS-POD-012 — A person can find out what they may do, before trying
**Status:** covered

- When a person asks what they may do in a pod, the system shall answer with
  their effective permissions, so an interface can hide what would be refused.
- The system shall keep that answer consistent with what the API actually
  enforces, so that anything reported as permitted succeeds and anything
  reported as denied is refused.

**Contracts:** `pod.permissions.me`, `pod.permissions.catalog`

### PS-POD-013 — A pod admin defines roles the built-in ones do not cover
**Status:** covered

- When a pod admin creates a custom role with a set of permissions, the system
  shall allow it to be assigned like a built-in one.
- If a person attempts to grant a role carrying permissions they do not
  themselves hold, then the system shall refuse — nobody may confer more than
  they have.
- When a pod admin changes a custom role's permissions, the system shall apply
  the change to everyone already holding it.

**Contracts:** `pod.roles.create`, `pod.roles.update`, `pod.role.permissions.replace`, `pod.role.permissions.get`

---

## Capability: Let people ask to join

### PS-POD-020 — A pod decides who may walk in
**Status:** covered

- Where a pod is invite-only, the system shall refuse every self-join and shall
  point the person at requesting access instead.
- Where a pod is open to the organization, the system shall let any member of
  its organization join directly, as a pod user.
- Where a pod is open to everyone, the system shall let any signed-in person
  join directly, adding them to the organization as an ordinary member on the
  way in.
- The system shall default a new pod to invite-only.

**Contracts:** `pod.join`, `pod.update`, `pod.member_joined`

### PS-POD-021 — A person asks for access and an admin decides
**Status:** covered

- When a person requests to join a pod, the system shall record the request as
  pending and notify the people who can decide it.
- When a pod admin or an organization owner approves a request, the system shall
  add the person to the pod with the role the approver chose, adding them to the
  organization first if they are not yet a member.
- If an organization editor who does not manage the pod attempts to list or
  decide its requests — their own included — then the system shall refuse.
  Deciding a request is adding a member, and answers to the same authority.
- If a person requests to join a pod they already belong to, then the system
  shall refuse rather than creating a request that cannot do anything.
- If someone attempts to decide a request that has already been decided, then
  the system shall refuse and shall say what it was decided as.
- The system shall accept a request from someone outside the pod's
  organization, since asking to be let in is the one thing an outsider is
  supposed to be able to do. This is a deliberate exception to PS-ACCESS-031:
  a pod that answered identically whether it existed or not could not be asked
  to join at all. What the exception costs — that a stranger can learn a pod id
  is real, and put a pending request in front of its admins — is bounded by the
  request being visible, decidable and refusable, and by there being nothing
  behind it to read.

**Contracts:** `pod.join_request.create`, `pod.join_request.approve`, `pod.join_request.list`, `pod.join_request.me`

### PS-POD-022 — Approving a request cannot be used to gain authority
**Status:** covered

- If an approver attempts to grant an organization role above their own, then
  the system shall refuse — approving a join request shall not become a side
  channel for minting owners or editors.
- If a pod admin who is only an ordinary organization member approves a request,
  then the system shall cap the organization role they may confer at ordinary
  member.
- If an approver attempts to grant pod roles carrying permissions they do not
  hold, then the system shall refuse. Only an organization owner is exempt, and
  no organization role short of owner changes that.

**Contracts:** `pod.join_request.approve`

### PS-POD-023 — A member finds the pods open to them, and joins one
**Status:** covered

- When a member of an organization asks which pods they can join, the system
  shall list every pod there that is open to the organization or to everyone
  and that they are not in.
- The system shall say of each pod offered what it is called, what it is for,
  who may join it, and how many people are in it.
- The system shall not offer a pod that is invite-only, has been deleted, or
  already has the person in it.
- When the person joins a pod they were offered, the system shall make them a
  pod user in it and stop offering it to them.
- Where the person owns the organization, the system shall offer them no pod,
  because they already reach every pod in it.
- If someone outside the organization asks which of its pods they can join,
  then the system shall refuse.

**Contracts:** `pod.list_joinable`, `pod.join`

### PS-POD-024 — A pod can take in everyone who joins its organization
**Status:** covered

- When someone joins an organization, however they joined, the system shall
  make them a pod user in every pod there that is set to take in new members.
- The system shall not add the people already in the organization when a pod is
  set to take in new members; it is among the pods open to them instead
  (PS-POD-023).
- The system shall not add an organization owner, who already reaches every
  pod.
- If a person sets a pod to take in new members while it is invite-only, or
  makes invite-only a pod that takes them in, then the system shall refuse and
  shall say the pod has to be open to the organization.
- If someone who may not decide who joins a pod attempts to set it to take in
  new members, then the system shall refuse.

> A pod that takes in everyone who arrives is the organization's front door,
> and an invite-only pod is a closed one. Refusing the combination, rather than
> quietly resolving it either way, leaves the choice of which one it is to the
> person making it.

**Contracts:** `pod.create`, `pod.update`

---

## Capability: See the pods you have

### PS-POD-030 — A person sees exactly the pods they may open
**Status:** covered

- When a person lists the pods in an organization, the system shall return every
  pod they are entitled to open, and no others.
- The system shall make listing and opening agree: any pod the system will open
  for a person shall appear in their list, and any pod absent from their list
  shall be refused when opened directly.
- When an organization owner lists pods, the system shall return every pod in
  the organization, because ownership carries responsibility for all of it.
- If a person attempts to open a pod in an organization they do not belong to,
  then the system shall refuse.

**Contracts:** `pod.list`, `pod.get`

### PS-POD-031 — A person sees their pods across every organization at once
**Status:** covered

- When a person asks for their navigation, the system shall return the
  organizations they belong to and the pods they may open in each, in one
  answer.
- The system shall keep navigation consistent with the per-organization pod
  list.

**Contracts:** `org.navigation`, `org.home`, `pod.list`

### PS-POD-032 — A person sees what waits on them, and what moved, across every pod at once
**Status:** planned

- When a person asks what is waiting on them in an organization, the system
  shall answer for every pod they may open in one list, oldest first: what their
  conversations asked them, the workflow steps assigned to them, and the
  contacts waiting on a person where they look after contacts.
- When a person asks what moved lately, the system shall answer for every pod
  they may open, including the quiet ones: their latest chats, the automations
  that ran, and how much work was done each day, counted in their own time
  zone.
- When a person asks for the organization's apps, the system shall list every
  app they may open, from every pod.
- The system shall remember when a person last looked at their organization's
  Home, for them alone and on every device, and shall never move that moment
  back.
- If a person is not a member of the organization, then the system shall
  refuse.
- The system shall not show a person, in any of these, a conversation that is
  someone else's, an app they could not open, or a contact in a pod where they
  may not see contacts.

**Contracts:** `org.needs_you.list`, `org.recents.get`, `org.apps.list`, `org.home.seen.get`, `org.home.seen.set`

### PS-POD-033 — History shows what a person's pods have been doing
**Status:** manual

> **Verified by:** opening History from the web app's sidebar on a stack
> running this build, signed in as a member of two pods: it lists that
> member's chats with each pod and the schedules and workflows that ran in the
> last 14 days, newest first under Today, Yesterday and earlier days, and a
> row opens the chat or the run. Choosing one pod shows only that pod's rows
> at `/history/{pod}`, which a pod's home also links to. `npm test` in
> `lemma-frontend` checks the rows, the days, the wording of each status and
> the addresses (`tests/history.test.ts`).

- When a person opens History, the system shall list what moved in every pod
  they may open over the last 14 days, newest first, grouped by the day it
  last moved in their own time zone: each of their chats, and each schedule
  and workflow that ran, once, with how many times it ran.
- Each row shall say which pod it is in, what it was, what kind of thing it
  was, how it ended in plain words, and when; and shall open the chat, the
  run, or the pod's schedules when nothing was started.
- A schedule whose filter turned an event away shall read as having had
  nothing to do, not as having gone wrong.
- A person shall be able to narrow History to one pod, to chats, to what ran
  by itself, or to what went wrong. One pod's history shall have an address
  of its own.
- History shall show a person nothing the overview would not: no one else's
  conversation, and nothing from a pod they may not open (PS-POD-032).

**Contracts:** `org.recents.get`

---

## Capability: Change and remove membership

### PS-POD-040 — Removing someone from a pod takes their access away immediately
**Status:** covered

- When a pod admin removes a member, the system shall revoke their access to the
  pod and everything in it on their next request.
- When a member is removed, the system shall also drop the resource grants they
  held through that membership, so that re-adding them does not silently restore
  old access.
- If someone who is neither an organization owner nor a pod admin attempts to
  remove another member, then the system shall refuse.

**Contracts:** `pod.member.remove`, `pod.permissions.me`

### PS-POD-041 — A pod always has at least one admin
**Status:** covered

- The system shall ensure every pod has at least one member who can administer
  it at all times.
- If removing a member or changing their roles would leave the pod with no
  admin, then the system shall refuse and shall say another admin must be
  appointed first.
- The system shall count anyone whose roles grant pod membership management, not
  only holders of the `POD_ADMIN` role, so that a pod using a custom role is not
  told it has one administrator when it has four.
- The system shall apply this to everyone, including an owner of the pod's
  organization, because reaching a pod through an organization role is not the
  same as the pod having an administrator.
**Contracts:** `pod.member.remove`, `pod.member.update_roles`

---

## Capability: Delete a pod

### PS-POD-050 — Deleting a pod stops the work it was doing
**Status:** covered

- When an organization owner or a pod admin deletes a pod, the system shall stop
  showing it and shall stop its schedules, surfaces, and other standing work.
- When a pod is deleted, the system shall record `pod.deleted`.
- When a pod is deleted, the system shall free its name for reuse.
- If a pod member who is not an admin attempts to delete the pod, then the
  system shall refuse.
- The system shall keep deleting safe to repeat, so that a retried deletion
  reports success rather than failing on the second attempt.

**Contracts:** `pod.delete`, `pod.deleted`

### PS-POD-051 — Deletion does not take unrelated things with it
**Status:** covered

- When a pod is deleted, the system shall leave the organization, its other
  pods, and their contents untouched.
- The system shall not remove the workspaces and data other pods depend on while
  cleaning up after a deleted one.

**Contracts:** `pod.delete`, `pod.deleted`

---

## Capability: Start from an empty pod

### PS-POD-060 — An empty place says what belongs there and offers a way to start
**Status:** manual

> **Verified by:** hiring a teammate in the sample workspace
> (`NEXT_PUBLIC_DATA=sample`), where a new hire starts empty, and opening its
> Pages, Apps, Tables, Files, Workflows and Chats, and a table with no rows.
> `npm test` in `lemma-frontend` checks the words and actions of every empty
> place (`tests/empty-copy.test.ts`) and that a sample hire starts empty
> (`tests/sample-hire.test.ts`).

- While a place in a pod — its pages, apps, tables, files, workflows,
  conversations, or a table's rows — holds nothing the person can see, the
  system shall say in one sentence what belongs there and who makes it, instead
  of showing an empty list.
- The system shall offer at least one way to make the first thing that needs no
  typing.
- When a person chooses a suggested request, the system shall put it in the
  chat box for them to change or send, and shall not send it.
- Where files can be added, the system shall accept files dropped on the place.
- When the pod's agent finishes work, the system shall show what it made in the
  pod's lists without a reload.
- If a search or a filter finds nothing, then the system shall say that nothing
  matches rather than that the place is empty.

### PS-POD-061 — An empty pod offers ways in, and a template is one of them
**Status:** manual

> **Verified by:** making a pod with the dialog (PS-POD-004) and opening it on
> a stack running this build: its page offers asking it, bringing things in, a
> template and making something. Adding the Support template says it is
> adding, then opens a chat asking the pod to set the template up, and the
> pod's Tables tab holds the template's table. `npm test` in `lemma-frontend`
> checks what counts as empty (`tests/lemma-space.test.ts`).

- While a pod holds no chat, table, app or file a person put there, its home
  shall offer four ways in: asking its agent, bringing things in from where the
  work is today, adding a template, and making a page, table or app or
  uploading files.
- Choosing a place to bring things in from, a table or an app shall put a
  request in the chat box for the person to send (PS-POD-060).
- When a person adds a template, the system shall bring the template's tables,
  skills and automations into that pod, say how far it has got, and then start
  a chat asking the pod's agent to set the template up.
- If a template cannot be added, then the system shall say why.

**Contracts:** `pod.bundle.templates`, `pod.bundle.import.start`, `pod.bundle.import.apply`, `pod.bundle.import.get`

### PS-POD-062 — Every page of a pod carries one header: its face, what it says, and seven tabs
**Status:** manual

> **Verified by:** opening each tab of a pod on a stack running this build:
> one header with the pod's face, its name, one sentence in the pod's own
> voice about what it is doing and whether anything needs the reader, its
> people, and the tabs Home, Chats, Apps, Tables, Pages, Files and About.
> `npm test` in `lemma-frontend` checks the row, that each tab has an address
> that reads back to it, and the sentence (`tests/lemma-space.test.ts`).

- Every page a pod is itself shall carry one header: the pod's face and name,
  one sentence in the pod's own voice, the people in it, and seven tabs in
  this order: Home, Chats, Apps, Tables, Pages, Files, About.
- The sentence shall say what the pod is doing and whether anything is waiting
  on the reader, counting only chats running now and things waiting on the
  reader. For a pod that holds nothing it shall say that the pod is new.
- No page of a pod shall ask for, or show, a result the pod owns, a target,
  a measure, or whether it is on track.
- A pod's home shall show its apps first, then what is waiting on the reader,
  then what it has been doing.
- No tab shall repeat the pod's name or its own name under that header.
- Each tab shall have an address of its own, so it can be linked to and
  reloaded.

**Contracts:** `pod.get`

### PS-POD-063 — About says what a pod is, and leads to each part of it
**Status:** manual

> **Verified by:** opening a pod's About tab on a stack running this build:
> it answers what the pod is for, who is in it, who can talk to it, what it
> does by itself, what it has been taught and what it is connected to, each in
> a few lines, and each answer links to the page that holds the rest. Opening
> its people from there shows "About" lit in the header and a link back. An old
> link to `/l/{pod}/about/connections` still opens the pod's connections, and
> one to `/l/{pod}/plan` opens About.
> `npm test` in `lemma-frontend` checks which pages are parts of About and how
> old links are read (`tests/lemma-space.test.ts`).

- A pod's About tab shall answer, in this order: what the pod is for, who is
  in it and who can find it, who can talk to it, what it does by itself, what
  it has been taught, and what it is connected to.
- What the pod is for shall be its description, and nothing else.
- What it has been taught shall be the notes in its memory that everyone in
  the pod shares, each opening its file, and shall link to everything it
  remembers and to its skills. Notes it keeps for one person shall not be
  listed there.
- Each answer shall be read from what the pod already keeps, shall list at
  most five things, and shall link to the page where the rest is kept and
  changed.
- The pod's people, its contacts, what it runs and its settings shall be
  parts of About: each shall keep an address of its own, shall keep
  About lit in the header, and shall offer the way back to About.
- A link to a part of the workspace's old About page shall open that part of
  the pod's settings.

**Contracts:** `pod.get`, `pod.member.list`, `schedule.list`, `file.list`

### PS-POD-064 — A pod says what it can do so far, and the one next step
**Status:** withdrawn

> Withdrawn: a checklist ending in "owns a result" made owning a result look
> like a step every pod had to reach. A pod's home and About no longer show
> it; the empty page's ways in (PS-POD-061) stand where it did.

### PS-POD-065 — A pod says hello to someone new to it, once
**Status:** manual

> **Verified by:** signing in as a member who joined a pod somebody else made
> and has no chat with it, on a stack running this build: the pod's home opens
> with a few sentences in the pod's own voice saying who it is, what it is
> for and how many others are in it, above what is waiting on them. "Got it"
> removes it, and it does not return on that browser. The pod's maker never
> sees it. `npm test` in `lemma-frontend` checks who counts as new and the
> words (`tests/lemma-space.test.ts`).

- When a member opens the home of a pod they did not make and have no chat
  with, the system shall show, once, a greeting in the pod's own voice: its
  name, what it is for when that has been said, and how many other people are
  in it.
- The greeting shall sit above what is waiting on that member in the pod.
- When the member dismisses it, the system shall not show it again on that
  browser.
- The system shall not show it to the pod's maker, or to a member who already
  has a chat with the pod.

**Contracts:** `pod.get`

---

## Capability: Give a pod one thing it owns

A pod can own one thing a person can check: a bar it holds every week ("Every
customer hears back within a day"), or a finish line by a date ("Twenty
design partners signed by the end of the quarter"). It is held to a target and counted by a measure, one
read-only query over the pod's own tables that answers a single number. The
plan for getting there is a file in the pod that people and its agent both
edit.

These are promises about the server, which keeps what a pod owns and its
weeks. Neither the web app nor the pod's agent asks for any of it or shows it
(PS-POD-062, PS-POD-073): a number will come with each kind of work, counted
from that work's own data.

### PS-POD-070 — A person starts a pod that owns something, in one step
**Status:** planned

- When a member of an organization starts a pod from a template, the system
  shall create the pod owning what the template counts, with the template's
  tables, skill and schedules, a first plan, and a conversation in which the
  pod's agent sets itself up.
- When a member starts a pod from a sentence in their own words, the system
  shall create the pod owning that sentence, with a first plan and a
  conversation in which its agent is asked to build what counts it.
- While a template's tables, skill and schedules are still being added, the
  system shall not start the pod's agent on that conversation.
- When a template's tables, skill and schedules have been added, or adding them
  has failed, the system shall start the pod's agent on that conversation.
- If any part of starting the pod fails, then the system shall leave no pod, no
  plan and no conversation behind.
- If the name asked for is taken in the organization, then the system shall give
  the pod the name with the next free number after it.
- If a template is a finish line and no date is given, then the system shall
  refuse.

**Contracts:** `pod.start`, `pod.bundle.templates`

### PS-POD-071 — Everyone in a pod sees the same number, week by week
**Status:** planned

- The system shall count a pod's measure over every row of the tables it names,
  so every member sees the same number, including members who may not open some
  of those rows.
- The system shall show a member the count and never the rows they may not
  open.
- The system shall keep, for each week, the last number the measure answered
  in it, with the target it was held to then.
- When a pod's measure is changed, the system shall start its weeks again.
- If the measure can no longer be read, then the system shall say why, and keep
  the week's last number.

**Contracts:** `outcome.standing`, `outcome.history`

### PS-POD-072 — Only someone who can read every row a measure counts may set it
**Status:** planned

- When a pod editor sets what the pod owns with a measure, the system shall read
  it once and answer what it counts before keeping it.
- If the person setting a measure may not read every row it counts, then the
  system shall refuse and keep what the pod owned before.
- If a measure is not one read-only query that answers a single number, then the
  system shall refuse and say what it answered instead.
- When an editor changes only what a pod owns in words, or its target, the
  system shall keep its measure without asking them to read every row.

**Contracts:** `outcome.set`, `outcome.clear`

### PS-POD-073 — A pod's agent knows what the pod owns and can set its measure
**Status:** withdrawn

> Withdrawn: a pod's agent is no longer told what the pod owns and has no tool
> to set it, so it never asks people to name a result or a target. What the
> pod has been taught is in its memory, which every conversation loads.

**Contracts:** `outcome.set`

---

## Not covered here

| Concern | Where it lives |
|---|---|
| Organizations, invitations, org roles | [Getting started](getting-started.md) |
| Granting one person access to one table or agent | [Sharing and permissions](sharing-and-permissions.md) |
| Making a pod from someone else's bundle | [Packaging and reuse](packaging-and-reuse.md) |
| What the pod's default agent may do on your behalf | [Agents and conversations](agents-and-conversations.md) |
