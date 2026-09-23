import type { Connectable } from "./connectable";
import type { Connector, ConnectorAccount } from "./accounts";
import type { AgentDetail, AgentDraft, AgentRow } from "./agents";
import type { Choice, Computer, Runtime } from "./runtimes";
import type { JoinPolicy, JoinRequest, OrgJoin } from "./joining";
import type { ScheduleDraft, ScheduleRun, StandingJob, TargetChoice } from "@/schedule/schedules";

/** The shapes the app renders. Both sources produce exactly these, so which
 *  one is in front of you is a configuration detail rather than a rewrite. */

export type MemberKind = "person" | "teammate";

export interface Member {
    id: string;
    name: string;
    initials: string;
    kind: MemberKind;
    role: string;
    /** What this member is allowed to do, in a person's words. */
    can: string;
    iconUrl?: string | null;
}

/** Who answers in a pod. Its own name and face, not the product's. */
export interface Persona {
    name: string;
    initials: string;
    iconUrl: string | null;
}

/** Chosen in the panel before a conversation exists: the pane goes empty and
 *  the first message is what actually creates it. */
export const NEW_CONVERSATION = "new";

export interface Org {
    id: string;
    name: string;
}

/** An invitation addressed to the person signed in.
 *
 *  Sending one is `invitations.invite`, in `org/people.tsx`. Reading the ones
 *  addressed to *you* is this, and it is the half that decides what arrival
 *  looks like: without it, somebody who has already been invited signs in and
 *  is told to go and ask to be invited.
 *
 *  It can name a teammate as well as an organization, which is the part that
 *  matters on arrival: an invitation carrying `podName` says who you were
 *  brought in to work with, so a colleague lands on that teammate rather than
 *  on whichever one happens to sort first in somebody else's rail. */
export interface Invitation {
    id: string;
    orgId: string;
    orgName: string;
    /** The teammate it names, when it names one. */
    podId: string | null;
    podName: string | null;
    podAbout: string | null;
    /** Who invited them to be, in the organization. */
    role: string;
}

/** A workspace about to be made.
 *
 *  `emailDomain` and the open join policy travel together and are set only for
 *  a company workspace on a domain the person plausibly owns — see
 *  `org/arrival.ts`, which refuses to pair them with a shared mailbox provider.
 *  Absent, the organization is invite-only.
 *
 *  `derived` is the backend's `resolve_name_conflicts`, whose own description
 *  names this exact use: a name the person did not type, where a 409 would be
 *  a dead end for somebody who never chose anything. */
export interface NewOrg {
    name: string;
    emailDomain?: string;
    derived?: boolean;
}

/** A channel this pod answers on. `mine` is whether the pod's own
 *  responder is the one behind it — a pod can carry surfaces bound to other
 *  agents, and saying "Lem is on Slack" when it is not would be a lie. */
export interface Surface {
    id: string;
    platform: string;
    name: string;
    mine: boolean;
    agentName: string;
    /** Where you actually reach it — a Telegram username, a WhatsApp number,
     *  an email address. The API calls this `reach.handle` and it is the same
     *  shape for every platform. */
    handle: string;
    email?: string;
    active: boolean;
}

/** A guided setup in flight: Lemma's manager bot makes you a bot of your own.
 *
 *  It is a three-legged thing — start it here, finish it in Telegram, and come
 *  back — so it has to survive as state rather than as a promise. `launchUrl`
 *  is where the person goes; `setupId` is what we ask about afterwards. */
export interface GuidedSetup {
    setupId: string;
    /** Deep link that opens Telegram on Lemma's manager bot. */
    launchUrl: string;
    managerBot: string;
    status: string;
    /** Set once the bot exists. */
    botUsername?: string;
    /** Deep link to the finished bot — the "say hi" at the end. */
    botLaunchUrl?: string;
    expiresAt: string;
    error?: string;
}

/** An account authorisation in flight.
 *
 *  Same three legs as the guided bot setup: start here, authorise on the
 *  provider's own consent page, come back. `authorizeUrl` is empty when the
 *  deployment cannot start one, which is the only case that still has to send
 *  somebody to the settings app. */
export interface AccountConnect {
    authorizeUrl: string;
    /** Account ids that existed before this started — how a new one is
     *  recognised when it lands. */
    before: string[];
}

/** A pod file, resolved far enough to put on screen. */
export interface FileContent {
    name: string;
    path: string;
    mime: string;
    size: number;
    /** How this app should draw it. */
    kind: "markdown" | "html" | "image" | "video" | "audio" | "pdf" | "text" | "binary";
    /** Set for markdown, text and inlined html. */
    text?: string;
    /** Why this is not drawn, when it is not. Only set on a file this app gave
     *  up on. It carries the actual reason because one fixed line — "Preview
     *  unavailable for this format" — answers every failure the same way: a
     *  readable format that was merely too big to inline then reads as a
     *  format nobody implemented, and sends people to the wrong code. */
    note?: string;

    /** Permanent authenticated deep link that opens this file in the Lemma
     *  frontend. Every file offers it, not only the ones drawn as a card —
     *  reading something inline is not the same as being able to leave with
     *  it. */
    appUrl?: string;
    /** Short-lived direct URL to the bytes. Expires. */
    rawUrl?: string;
}

export interface PodDetail {
    members: Member[];
    teammate: Persona;
    subtitle: string;
}

export interface Pod {
    id: string;
    orgId: string;
    name: string;
    /** A pod may carry its own icon; without one it wears a generated orb. */
    iconUrl: string | null;
    /** The agent that answers here. */
    teammate: Persona;
    /** "with Priya and you" — who is in here, said the way a person would. */
    subtitle: string;
    members: Member[];
    /** Set when something in here is waiting on a person. */
    waiting: string;
}

/** Conversation is a tab like any other. It is always first, and it is the
 *  only one that carries a composer. */
export interface LibraryItem { id: string; name: string; kind: "file" | "folder" | "table"; path: string; updated: string; detail: string }
export interface ResourcePage<T> { items: T[]; next?: string | null }

/** A link that works without a Lemma account.
 *
 *  Bounded on purpose, and the bounds are the product: the platform mints a
 *  short code, streams the bytes *through* the backend rather than redirecting
 *  to object storage, and stops serving once either bound is reached. That is
 *  what makes "share this outside the pod" something you can offer without
 *  handing over unbounded egress. Both are clamped server-side — three hours
 *  and fifty opens by default, a day and a hundred at the ceiling — so what
 *  comes back is the truth rather than what was asked for. */
export interface SharedLink {
    /** `{api}/s/{code}` — the bytes. */
    rawUrl: string;
    /** The same document, rendered, for a person rather than a browser. */
    readUrl: string;
    code: string;
    expiresAt: string;
    maxHits: number;
}

export type Tab =
    | { id: "library"; kind: "library"; label: string }
    | { id: string; kind: "table"; label: string; name: string }
    | { id: "conversation"; kind: "conversation"; label: string }
    | { id: string; kind: "app"; label: string; url: string; status: string }
    | { id: "profile"; kind: "profile"; label: string }
    /** The agents behind this teammate: the one answering you, and the ones it
     *  hands work to. In the strip rather than opened on demand, because
     *  "which agents are in here" is a question about the pod itself — the
     *  same standing as its Library. */
    /** Opened on demand from the history panel, then it stays in the strip
     *  for that teammate the way an opened browser tab does. */
    | { id: "history"; kind: "history"; label: string }
    /** A file the agent showed, opened onto the stage. Same idea: you asked
     *  for it, so it stays until you close it. */
    | { id: string; kind: "file"; label: string; path: string }
    /** One row, on its own page — reached from its table or from search, and
     *  worth a tab because what it is attached to is worth navigating. */
    | { id: string; kind: "record"; label: string; table: string; recordId: string }
    /** The sandbox every teammate runs its shell in. It is the one view here
     *  that is not about this pod — the machine belongs to the person, not the
     *  teammate — but it opens on the folder the open conversation works in,
     *  which is what makes it worth reaching from beside that conversation. */
    | { id: "computer"; kind: "computer"; label: string };

/* ── conversation ──────────────────────────────────────────────────── */

/** A message as the API gives it. The sample source produces the same shape
 *  so both paths go through one parser. */
export interface Message {
    id?: string;
    role?: string;
    kind?: string;
    text?: string | null;
    tool_name?: string | null;
    tool_args?: unknown;
    /** What a TOOL_RETURN came back with. The decision on an approval lives
     *  here, which is the only way to know a pause has been answered. */
    tool_result?: unknown;
    /** Ties a TOOL_RETURN to its TOOL_CALL — and, for `request_approval`, it
     *  is also the approval id. See `thread/approval.ts`. */
    tool_call_id?: string | null;
    sequence?: number;
    created_at?: string;
}

/** One entry in a teammate's history. */
export interface ConversationRef {
    id: string;
    title: string;
    at: string;
    kind: string;
    /** The resource this conversation is bound to, if it is one a resource
     *  carries. Its front door is that resource, so the recent panel leaves it
     *  out — see `unbound`. */
    boundTo?: string | null;
}

export interface Conversation {
    id: string | null;
    title: string;
    /** Newest run state, so the thread can say what is happening right now. */
    status: string | null;
    messages: Message[];
}

/* ── profile ───────────────────────────────────────────────────────── */

/** A standing arrangement: something this teammate does without being asked
 *  each time. It is the closest thing an agent has to a job, so the profile
 *  lists them where a person would list positions held. */
export interface Commitment {
    id: string;
    title: string;
    detail: string;
    /** "Every weekday at 09:00", "When mail arrives" — said in words. */
    cadence: string;
    since: string;
    active: boolean;
    last: string;
}

/** Something this teammate built and runs. */
export interface Project {
    id: string;
    name: string;
    description: string;
    status: string;
    /** The tab id in this pod, so the card can open it rather than leave. */
    tabId: string;
}

/** A capability, said the way a person would say it rather than the way the
 *  API spells it. `WORKSPACE_CLI` is not a skill; "works a computer" is. */
export interface Skill {
    id: string;
    label: string;
    blurb: string;
}

/** Everything the profile says about a teammate, and all of it comes from
 *  somewhere real — a pod's own description, its default agent's instruction
 *  and toolsets, its schedules, its apps. Nothing here is decoration with
 *  invented numbers behind it. */
export interface Profile {
    podId: string;
    name: string;
    iconUrl: string | null;
    /** The pod's description. Most pods have none, so the page composes a
     *  true sentence from what it does instead of leaving a hole. */
    headline: string;
    /** ISO, from the pod's own `created_at`. */
    joined: string;
    /** The default agent's instruction — the only long prose a pod writes
     *  about itself, so it is the About section. */
    about: string;
    skills: Skill[];
    /** What it is allowed to do without stopping to ask. */
    permits: string[];
    commitments: Commitment[];
    projects: Project[];
    /** Counted, not estimated; a zero is shown as a zero. */
    counts: { tables: number; functions: number; workflows: number };
    /** Failed reads must not be presented as empty resources or zero counts. */
    unavailable?: Array<"tools" | "schedules" | "apps" | "tables" | "functions" | "workflows">;
}

/* ── the seam ──────────────────────────────────────────────────────── */

export interface PodSource {
    /** Shown in the corner so it is never ambiguous what you are looking at. */
    label: string;
    listLibrary(podId: string, kind: "files" | "tables", directory: string, page?: string): Promise<ResourcePage<LibraryItem>>;
    /** Mint a public link to one document. Ask for a lifetime and a number of
     *  opens; the platform decides what it will actually allow. */
    shareFile(podId: string, path: string, options?: { expiresSeconds?: number; maxHits?: number }): Promise<SharedLink>;
    tableColumns(podId: string, name: string): Promise<{ name: string; system?: boolean }[]>;
    /** How many rows the table actually has, or `null` when it will not say.
     *
     *  Rows arrive fifty at a time, so nothing else on screen can tell "twenty-six
     *  tasks" from "the first twenty-six of four thousand" — and those want
     *  opposite screens. Asked once, up front, so the view can settle on a shape
     *  before anybody is reading it rather than rearranging underneath them. */
    tableCount(podId: string, name: string): Promise<number | null>;
    /** One row, by whatever the table calls its key. */
    tableRecord(podId: string, name: string, id: string): Promise<Record<string, unknown> | null>;
    /** A table's shape, including what its columns point at. */
    tableShape(podId: string, name: string): Promise<unknown>;
    /** Every table's shape, because what points *here* is declared over there. */
    tableShapes(podId: string): Promise<unknown[]>;
    /** Rows of `table` whose `column` holds this id. */
    referencing(podId: string, table: string, column: string, id: string, limit: number): Promise<Record<string, unknown>[]>;
    tableRows(podId: string, name: string, page?: string): Promise<ResourcePage<Record<string, unknown>>>;
    /** Run one read-only SELECT inside this pod's datastore.
     *
     *  `truncated` is not decoration. The row cap cuts a result short without
     *  saying so anywhere in the rows themselves, and a capped answer read as
     *  a complete one is a wrong number said out loud. */
    runQuery(podId: string, sql: string): Promise<{ items: Record<string, unknown>[]; truncated: boolean }>;
    listOrgs(): Promise<Org[]>;
    /** Invitations addressed to the person signed in, still open. */
    myInvitations(): Promise<Invitation[]>;
    /** Take one up. The organization appears in `listOrgs` afterwards. */
    acceptInvitation(invitationId: string): Promise<void>;
    /** Organizations this person's email domain is allowed to let itself into.
     *  Empty is the common answer and not an error. */
    suggestedOrgs(): Promise<Org[]>;
    joinOrg(orgId: string): Promise<void>;
    createOrg(wanted: NewOrg): Promise<Org>;
    listPods(orgId: string): Promise<Pod[]>;
    listTabs(podId: string): Promise<Tab[]>;
    createPod(orgId: string, name: string, description?: string): Promise<Pod>;
    /** Set (or clear) a teammate's face. An emoji, a URL, or the
     *  `lemma-identity:N` sentinel that picks which generated creature. */
    setPodIcon(podId: string, iconUrl: string | null): Promise<void>;
    /** Change a teammate's name.
     *
     *  The pod's name *is* the name — the rail, the header, the badge, the
     *  masthead and the address a surface answers on all read it — so there
     *  is one of these rather than a display name laid over a real one that
     *  people would then find in an error message. */
    renamePod(podId: string, name: string): Promise<void>;
    /** Put a picture somewhere the platform will serve it, and hand back the
     *  URL to store in `icon_url`. */
    uploadIcon(file: File): Promise<string>;
    /** Who is in a teammate and who answers there. Fetched only for the one
     *  you are looking at — doing it for the whole list cost 22 requests
     *  before the sidebar could draw. */
    getPodDetail(podId: string, podName: string, podIcon?: string | null): Promise<PodDetail>;
    listSurfaces(podId: string): Promise<Surface[]>;
    /** Every platform this pod could be reached on, and what each would cost
     *  to set up. Read before anything is clicked — see `connectable.ts`. */
    listConnectable(podId: string): Promise<Connectable[]>;
    /** The one-click path: a Lemma-run identity answers, with no account of
     *  yours. Returns the surface, which already carries the address. */
    connectSystem(podId: string, platform: string): Promise<Surface>;
    /** Everything a teammate in this organization could run on: the provider
     *  keys it has bought, and the coding agents added off paired computers.
     *  Archived ones come too — the ledger hides them behind a toggle rather
     *  than pretending they were deleted. */
    listRuntimes(orgId: string): Promise<Runtime[]>;
    /** What this organization would run on if nobody chose — the catalog's
     *  own default, so "inherit" can say what it inherits. */
    defaultRuntime(orgId: string): Promise<Choice | null>;
    /** The computers this person has paired, each with the coding agents it
     *  found on itself. Pairing happens in the Lemma desktop app; this app
     *  reads what that produced. */
    listComputers(): Promise<Computer[]>;
    /** Buy-your-own: a base URL and a key, and the models it serves. */
    addProviderKey(orgId: string, key: {
        protocol: "openai" | "anthropic";
        name: string;
        baseUrl: string;
        apiKey: string;
        models: string[];
    }): Promise<void>;
    /** Make a coding agent on a paired computer pickable. Bound to the live
     *  harness, so the computer has to be awake and the agent ready. */
    addLocalAgent(orgId: string, harnessId: string, agent: {
        name: string;
        model: string;
        shared: boolean;
    }): Promise<void>;
    /** Retire one. Not a delete — history stays readable, and it can come
     *  back. */
    archiveRuntime(orgId: string, runtimeId: string): Promise<void>;
    restoreRuntime(orgId: string, runtimeId: string): Promise<void>;
    /** What this teammate runs on when nothing more specific says otherwise.
     *  `null` means it has never been told, and inherits. */
    getPodRuntime(podId: string): Promise<Choice | null>;
    setPodRuntime(podId: string, choice: Choice | null): Promise<void>;
    /** Who may add themselves to this teammate without anybody letting them.
     *  A pod that has never been told answers `"invited"`, which is what the
     *  platform does with an unset policy. */
    getPodJoin(podId: string): Promise<JoinPolicy>;
    setPodJoin(podId: string, policy: JoinPolicy): Promise<void>;
    /** Whether the signed-in person has already knocked on this teammate's
     *  door, so a second visit says "waiting" instead of offering the ask
     *  again. Null when they have never asked. */
    myJoinRequest(podId: string): Promise<JoinRequest | null>;
    /** Knock. What this becomes is the pod's own policy and not this app's
     *  call: a door already open to the asker admits them on the spot and
     *  answers `approved`, a shut one answers `pending` and emails whoever
     *  runs the pod. It is also the only call that can tell a teammate who is
     *  not yours from one that does not exist — the platform answers 404 for
     *  the second and a real request for the first, deliberately, because the
     *  alternative leaves the one route into a pod open only to people already
     *  inside it. */
    askToJoin(podId: string): Promise<JoinRequest>;
    /** Who is waiting at this teammate's door, for whoever runs it. */
    listJoinRequests(podId: string): Promise<JoinRequest[]>;
    /** Let somebody in.
     *
     *  There is no matching refusal, and that is the platform's shape rather
     *  than an omission here: `REJECTED` exists as a status and no endpoint
     *  sets it. So the UI offers admitting and says plainly that declining is
     *  not something it can do, instead of drawing a Decline button that would
     *  have to fail. */
    admitToPod(podId: string, requestId: string): Promise<JoinRequest>;
    /** And the same question one level up. An organization that has never been
     *  told answers `"invited"` — and so does one set to admit an email domain
     *  without having been given one, because that rule admits nobody. */
    getOrgJoin(orgId: string): Promise<OrgJoin>;
    setOrgJoin(orgId: string, join: OrgJoin): Promise<void>;
    /** Every connector this deployment catalogues. */
    listConnectors(): Promise<Connector[]>;
    /** The organization's connected accounts, across every connector. */
    listAccounts(orgId: string): Promise<ConnectorAccount[]>;
    /** Forget an account. Anything pinned to it stops working, so the UI
     *  confirms first. */
    disconnectAccount(orgId: string, accountId: string): Promise<void>;
    /** Every surface the signed-in person can see, across every pod they
     *  belong to. One call, and the only way this app can learn that another
     *  teammate already answers on a platform — the surfaces list is
     *  per-pod, and the available-surfaces catalog reports the shared-identity
     *  claim but says nothing about a connected account's. */
    listMySurfaces(): Promise<{ platform: string; podId: string; name: string }[]>;
    /** The Slack app manifest for a bot that answers as this teammate alone.
     *  One Slack app is one bot user, so the name is set here or not at all. */
    slackManifest(agentName: string): Promise<Record<string, unknown>>;
    /** Register an app the organization made itself, so its credentials are
     *  what the authorisation runs against. Returns the auth config to
     *  authorise. */
    addCustomApp(
        orgId: string,
        connectorId: string,
        name: string,
        config: Record<string, unknown>,
    ): Promise<string>;
    /** Begin authorising an account with the provider. Finishing happens on
     *  their consent page, and the account appears here afterwards. */
    startAccount(orgId: string, connectorId: string, authConfigId?: string): Promise<AccountConnect>;
    /** The account this authorisation produced, once it exists and is usable.
     *  Empty until then. */
    findAccount(orgId: string, connectorId: string, before: string[]): Promise<string>;
    /** Put a surface on an account that has just been authorised. */
    connectAccount(podId: string, platform: string, accountId: string): Promise<Surface>;
    /** Begin the guided path. Finishing happens in Telegram. */
    startGuided(podId: string, platform: string): Promise<GuidedSetup>;
    /** Has the guided setup finished? */
    checkGuided(podId: string, setupId: string): Promise<GuidedSetup>;
    /** Stop answering here. Frees the account, and the org's system claim. */
    disconnect(podId: string, surfaceName: string): Promise<void>;
    downloadFile(podId: string, path: string): Promise<Blob>;
    readFile(podId: string, path: string): Promise<FileContent>;
    /** Write a text file back, replacing what is there.
     *
     *  The whole file every time, because that is what the editor has: it
     *  holds a document as a tree, not as a diff against the bytes it was
     *  handed. Only markdown reaches this today — see `editableKind` in
     *  `thread/document-save.ts` for why the other text formats do not.
     */
    writeFile(podId: string, path: string, text: string): Promise<void>;
    /** A signed URL the widget iframe can load. The serve route is
     *  authenticated and injects the runtime config the widget's browser SDK
     *  needs, which inline HTML in an iframe can never have. */
    widgetEmbedUrl(podId: string, conversationId: string, toolCallId: string): Promise<string>;
    listConversations(podId: string): Promise<ConversationRef[]>;
    /** The call threads hanging off one conversation.
     *
     *  A call runs in a conversation of its own, parented to whatever was
     *  open. The default listing does not return children, which is right for
     *  it — they are not things you start, they are things that happened
     *  inside something you started — so they are asked for by parent, and
     *  only for the thread being read. */
    listCallThreads(podId: string, parentId: string): Promise<ConversationRef[]>;
    getConversation(podId: string, teammate?: Persona, conversationId?: string | null): Promise<Conversation>;
    /** The about-me page: who this teammate is, what it is standing on. */
    getProfile(podId: string): Promise<Profile>;
    /** Every agent in this pod, the one that answers you included. The list
     *  shape is lean by design — no instruction, no runtime, no schemas — so
     *  opening one costs `getAgent`. */
    listAgents(podId: string): Promise<AgentRow[]>;
    getAgent(podId: string, name: string): Promise<AgentDetail>;
    /** PATCH, sending only what changed. Returns the agent as saved, because
     *  the response carries a re-projected `allowed_actions` and the screen
     *  should redraw off that rather than off what it sent. */
    updateAgent(podId: string, name: string, before: AgentDraft, after: AgentDraft): Promise<AgentDetail>;
    /** Gone, along with every channel it answered on. */
    deleteAgent(podId: string, name: string): Promise<void>;
    /** The standing work: everything this teammate does without being asked.
     *
     *  Richer than `Profile.commitments`, which is the one-line version the
     *  profile header counts. This carries what fires each one, what it runs,
     *  and whether the last fire worked — the fields the profile threw away. */
    listSchedules(podId: string): Promise<StandingJob[]>;
    /** Recent firings of one schedule, newest first. */
    listScheduleRuns(podId: string, scheduleId: string): Promise<ScheduleRun[]>;
    /** Pause or resume. One PATCH, and resuming clears the failure count the
     *  breaker was holding — see `is_explicit_reactivation` in the schedule
     *  service — so the row is re-read from what comes back. */
    setScheduleActive(podId: string, scheduleId: string, active: boolean): Promise<StandingJob>;
    /** Run a failed firing again, with the event it originally carried. */
    retryScheduleRun(podId: string, scheduleId: string, runId: string): Promise<ScheduleRun>;
    createSchedule(podId: string, draft: ScheduleDraft): Promise<StandingJob>;
    /** What a new schedule could wake: this pod's agents and its workflows. */
    scheduleTargets(podId: string): Promise<TargetChoice[]>;
    /** `into` is a conversation id, `null` for the one already open, or
     *  NEW_CONVERSATION to start a fresh one with this first message. */
    send(podId: string, text: string, teammate?: Persona, into?: string | null): Promise<Conversation>;
}
