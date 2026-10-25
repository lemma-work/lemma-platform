/** A pod's connectors, and the typed refusals they answer with.
 *
 *  A pod declares the connectors it works through, each addressed by a slug
 *  (`gmail`, `support-inbox`). The connector, not the caller, decides whose
 *  account a call runs as: a PER_MEMBER one uses the acting person's own, a
 *  SHARED one the account its owner shared with the pod. So nothing here picks
 *  an account any more. A schedule, a surface or a workflow names a slug, and
 *  when the connector cannot run for this person the server says why with a
 *  code and, where the person can fix it, a `connect_url`.
 *
 *  Pure, for the reason `accounts.ts` is: every field here is a wire field.
 */

export type PodConnectorMode = "PER_MEMBER" | "SHARED";
export type PodConnectorStatus = "READY" | "NEEDS_ACCOUNT" | "UNAVAILABLE";

export type PodConnectorVisibility = "POD" | "RESTRICTED";

export interface PodConnector {
    /** The row's own id. Grants and bundles name a connector by slug; this is
     *  for keys and nothing else. */
    id: string;
    slug: string;
    /** The catalog connector behind it — `slack`, `github`. Empty when the
     *  caller may not read the installation. */
    connectorId: string;
    /** The organization installation it runs through. */
    authConfigId: string;
    /** That installation's name, as the organization called it. */
    installationName: string;
    mode: PodConnectorMode;
    status: PodConnectorStatus;
    /** What the pod uses it for, in its editor's words. */
    purpose: string;
    /** PER_MEMBER: every member who has no account is asked to connect one. */
    required: boolean;
    /** For a SHARED connector: whose account it runs as, by name or email. */
    sharedAccount: string;
    /** For a SHARED connector: the pod account it runs as, and its owner. */
    sharedPodAccountId: string;
    sharedOwnerId: string;
    visibility: PodConnectorVisibility;
    /** Whether I, reading it, could run calls through it once I have an
     *  account on its installation: I hold `connector.use` on it, and a
     *  RESTRICTED one does not leave me out. The server's `can_use`; null
     *  when it does not say. A viewer holds no `connector.use`, so asking a
     *  viewer to connect is asking for an account nothing will use. */
    canUse: boolean | null;
}

/** Why a member is still awaited: no account at all, or one that needs its
 *  owner to sign in again. */
export type MemberStanding = "MISSING" | "REAUTH_REQUIRED";

/** A member a connector is waiting on — who, never which account. */
export interface AwaitedMember {
    userId: string;
    podMemberId: string;
    /** Their name, else their email: what the editor calls them. */
    name: string;
    standing: MemberStanding;
}

export interface Coverage {
    /** Members of the pod who may use the connector. */
    eligible: number;
    /** Of those, how many have a working account. */
    connected: number;
    waitingOn: AwaitedMember[];
    /** The pod has more members than the server counted. */
    truncated: boolean;
}

const MODES: PodConnectorMode[] = ["PER_MEMBER", "SHARED"];
const STATUSES: PodConnectorStatus[] = ["READY", "NEEDS_ACCOUNT", "UNAVAILABLE"];

function record(value: unknown): Record<string, unknown> {
    return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function text(value: unknown): string {
    return typeof value === "string" ? value.trim() : "";
}

export function readPodConnector(raw: unknown): PodConnector | null {
    const row = record(raw);
    const slug = text(row["slug"]);
    if (!slug) return null;
    const mode = text(row["mode"]).toUpperCase() as PodConnectorMode;
    const status = text(row["status"]).toUpperCase() as PodConnectorStatus;
    const shared = record(row["shared_account"]);
    return {
        id: text(row["id"]) || slug,
        slug,
        connectorId: text(row["connector_id"]),
        authConfigId: text(row["auth_config_id"]),
        installationName: text(row["installation_name"]),
        mode: MODES.includes(mode) ? mode : "PER_MEMBER",
        /* A status this client does not know is not one to offer as ready. */
        status: STATUSES.includes(status) ? status : "UNAVAILABLE",
        purpose: text(row["purpose"]),
        required: row["required"] === true,
        sharedAccount: text(shared["display_name"]) || text(shared["email"]),
        sharedPodAccountId: text(row["shared_pod_account_id"]) || text(shared["pod_account_id"]),
        sharedOwnerId: text(shared["owner_user_id"]),
        visibility: text(row["visibility"]).toUpperCase() === "RESTRICTED" ? "RESTRICTED" : "POD",
        canUse: typeof row["can_use"] === "boolean" ? row["can_use"] : null,
    };
}

function count(value: unknown): number {
    return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : 0;
}

function readAwaited(raw: unknown): AwaitedMember[] {
    return (Array.isArray(raw) ? raw : []).map((entry) => {
        const one = record(entry);
        return {
            userId: text(one["user_id"]),
            podMemberId: text(one["pod_member_id"]),
            name: text(one["name"]) || text(one["email"]) || "Someone",
            standing: (text(one["standing"]).toUpperCase() === "REAUTH_REQUIRED" ? "REAUTH_REQUIRED" : "MISSING") as MemberStanding,
        };
    }).filter((one) => one.userId);
}

/** Whether to ask the server who a connector is waiting on: only for a
 *  required per-member connector, and only for someone who may manage the
 *  pod's connectors — `GET …/connectors/{slug}/coverage` refuses anyone else.
 *  Listings never carry it (their `member_coverage` is always null): it
 *  weighs each member's own access, so it is asked one connector at a time. */
export function wantsCoverage(connector: Pick<PodConnector, "mode" | "required">, mayManage: boolean): boolean {
    return mayManage && connector.required && connector.mode === "PER_MEMBER";
}

/** The answer of `GET /pods/{pod}/connectors/{slug}/coverage`. */
export function readCoverage(raw: unknown): Coverage {
    const row = record(raw);
    return {
        eligible: count(row["eligible"]),
        connected: count(row["connected"]),
        waitingOn: readAwaited(row["waiting_on"]),
        truncated: row["truncated"] === true,
    };
}

/** "5/7 connected", or "Everyone connected" once nobody is awaited — never
 *  when only part of the pod was counted. */
export function coverageLine(coverage: Coverage): string {
    if (coverage.eligible > 0 && coverage.connected >= coverage.eligible && !coverage.truncated) return "Everyone connected";
    return coverage.connected + "/" + coverage.eligible + " connected";
}

/** Said beside the count when the server stopped counting: the pod has more
 *  members than it weighed, so the count is of some of them. Empty otherwise. */
export function truncatedLine(coverage: Coverage): string {
    return coverage.truncated ? "Only some of the pod’s members were counted" : "";
}

function names(members: AwaitedMember[], most = 5): string {
    const said = members.slice(0, most).map((one) => one.name);
    const rest = members.length - said.length;
    return said.join(", ") + (rest > 0 ? " and " + rest + " more" : "");
}

/** Who it waits on: "Waiting on Bob, and carol@acme.com to reconnect".
 *  Empty when it waits on nobody. */
export function waitingLine(coverage: Coverage): string {
    const missing = coverage.waitingOn.filter((one) => one.standing === "MISSING");
    const again = coverage.waitingOn.filter((one) => one.standing === "REAUTH_REQUIRED");
    const parts = [
        ...(missing.length ? [names(missing)] : []),
        ...(again.length ? [names(again) + " to reconnect"] : []),
    ];
    return parts.length ? "Waiting on " + parts.join(", and ") : "";
}

/** What "Ask members to connect" did. */
export interface AskOutcome {
    asked: AwaitedMember[];
    /** Asked about this connector in the last day already, so not again. */
    askedRecently: AwaitedMember[];
    /** Not asked: whether they were asked today could not be checked. */
    notAsked: AwaitedMember[];
    connectUrl: string;
}

export function readAskOutcome(raw: unknown): AskOutcome {
    const row = record(raw);
    return {
        asked: readAwaited(row["asked"]),
        askedRecently: readAwaited(row["asked_recently"]),
        notAsked: readAwaited(row["not_asked"]),
        connectUrl: text(row["connect_url"]),
    };
}

/** The sentence after asking: who was asked, and who was asked already. */
export function askedLine(outcome: AskOutcome): string {
    const lines: string[] = [];
    if (outcome.asked.length) lines.push("Asked " + names(outcome.asked) + ".");
    if (outcome.askedRecently.length) {
        lines.push(names(outcome.askedRecently) + (outcome.askedRecently.length === 1 ? " was" : " were") + " asked in the last day.");
    }
    if (outcome.notAsked.length) {
        lines.push("Couldn’t ask " + names(outcome.notAsked) + " just now — try again shortly.");
    }
    return lines.join(" ") || "Nobody to ask.";
}

export function readPodConnectors(raw: unknown): PodConnector[] {
    const items = Array.isArray(raw) ? raw : record(raw)["items"];
    return (Array.isArray(items) ? items : []).map(readPodConnector).filter((one): one is PodConnector => one !== null);
}

/** The pod's connectors for one catalog connector, the ready ones first. A
 *  surface runs as one account for everyone it answers, so it only ever takes
 *  a SHARED connector; pass `mode` to say so. */
export function connectorsFor(list: PodConnector[], connectorId: string, mode?: PodConnectorMode): PodConnector[] {
    return list
        .filter((one) => (!connectorId || one.connectorId === connectorId) && (!mode || one.mode === mode))
        .sort((a, b) => Number(b.status === "READY") - Number(a.status === "READY"));
}

/** Where a person connects, shares or reconnects an account for this pod's
 *  connectors — opened on one connector when there is one to name. It is the
 *  address the server's own `connect_url` points at. */
export function connectionsPath(podId: string, slug?: string): string {
    return "/pods/" + encodeURIComponent(podId) + "/connections" + (slug ? "?connector=" + encodeURIComponent(slug) : "");
}

/** The same place inside the workspace: a teammate's Connections part, for
 *  a link that should stay in the app rather than load it again. */
export function connectionsPlace(podId: string, slug?: string): string {
    return "/t/" + encodeURIComponent(podId) + "/about/connections" + (slug ? "?connector=" + encodeURIComponent(slug) : "");
}

/** A slug for a new connector, free in this pod: `base` made to read the same
 *  in a URL, a YAML key and a tool argument (the server's own pattern), then
 *  `-2`, `-3` while taken — what the server does when it shares a bot it made. */
export function freshSlug(base: string, taken: string[]): string {
    const root = base.toLowerCase().replace(/[^a-z0-9_-]+/g, "-").replace(/^[-_]+|-+$/g, "").slice(0, 56) || "account";
    const used = new Set(taken);
    let slug = root;
    for (let n = 2; used.has(slug); n += 1) slug = root + "-" + n;
    return slug;
}

/** A connector's state, said for a person. Empty when it is ready. */
export function statusNote(connector: Pick<PodConnector, "status">): string {
    if (connector.status === "UNAVAILABLE") return "Turned off for this organization";
    if (connector.status === "NEEDS_ACCOUNT") return "No account shared yet";
    return "";
}

/** One line naming the connector in a picker: its slug, and for a shared one
 *  whose account it runs as. */
export function connectorLabel(connector: PodConnector): string {
    const whose = connector.mode === "SHARED"
        ? (connector.sharedAccount ? " — runs as " + connector.sharedAccount : "")
        : " — each person's own account";
    const note = statusNote(connector);
    return connector.slug + whose + (note ? " (" + note.toLowerCase() + ")" : "");
}

/* ── the typed refusals ─────────────────────────────────────────────── */

/** Every code a pod connector refuses with. Each but the not-found carries the
 *  slug in `details.pod_connector`; CONNECTION_REQUIRED and REAUTH_REQUIRED
 *  carry a `connect_url` when the person reading can fix it. */
export const CONNECTION_CODES = [
    "CONNECTION_REQUIRED",
    "REAUTH_REQUIRED",
    "CONNECTOR_NEEDS_ACCOUNT",
    "CONNECTOR_UNAVAILABLE",
    "CONNECTION_REQUIRES_PERSON",
    "POD_CONNECTOR_NOT_FOUND",
] as const;

export type ConnectionCode = (typeof CONNECTION_CODES)[number];

export interface ConnectionProblem {
    code: ConnectionCode;
    message: string;
    /** Where to fix it, or empty when the person reading cannot. */
    connectUrl: string;
    /** The slug it was about, when the server named one. */
    podConnector: string;
}

/** The connector refusal inside a failed call, or null for any other failure.
 *  Read off what the SDK's errors carry: `code`, `message`, and the response's
 *  `details`. */
export function connectionProblem(error: unknown): ConnectionProblem | null {
    if (!error || typeof error !== "object") return null;
    const said = error as { code?: unknown; message?: unknown; details?: unknown };
    const code = text(said.code) as ConnectionCode;
    if (!CONNECTION_CODES.includes(code)) return null;
    const details = record(said.details);
    return {
        code,
        message: text(said.message),
        connectUrl: text(details["connect_url"]),
        podConnector: text(details["pod_connector"]),
    };
}

/** What the connect link says: reconnecting an expired sign-in is a different
 *  act from connecting an account for the first time. */
export function connectLinkLabel(code: string): string {
    if (code === "REAUTH_REQUIRED") return "Reconnect";
    // Nobody's account is shared yet: the fix is someone sharing one, which
    // need not be the person reading this.
    if (code === "CONNECTOR_NEEDS_ACCOUNT") return "Open connections";
    return "Connect your account";
}

/** A failed call's sentence, and the link that fixes it when there is one.
 *  Any typed refusal that carries a `connect_url` gets its link, not only the
 *  connection codes: "use for everyone as…" can be refused for an account
 *  that needs connecting, under a code of its own. */
export function failureOf(error: unknown, fallback: string): { message: string; connectUrl: string; code: string } {
    const typed = connectionProblem(error);
    if (typed) return { message: typed.message || fallback, connectUrl: typed.connectUrl, code: typed.code };
    const message = error instanceof Error ? error.message : typeof error === "string" ? error : "";
    const said = error && typeof error === "object" ? error as { code?: unknown; details?: unknown } : {};
    return { message: message || fallback, connectUrl: text(record(said.details)["connect_url"]), code: text(said.code) };
}

/* ── a connector, said for the person reading ───────────────────────── */

/** The pattern the server holds a slug to: what reads the same in a URL, a
 *  YAML key and a tool argument. */
export const SLUG_PATTERN = /^[a-z0-9][a-z0-9_-]{0,62}$/;

/** Why a slug cannot be used here, or empty when it can. */
export function slugProblem(slug: string, taken: string[]): string {
    if (!slug) return "Give it a name apps and agents can call it by.";
    if (!SLUG_PATTERN.test(slug)) return "Lowercase letters, digits, - and _ only, starting with a letter or digit.";
    if (taken.includes(slug)) return "This teammate already has a connector called " + slug + ".";
    return "";
}

/** The badge beside a connector: whose account a call through it runs as.
 *  `owner` is the shared account's owner by name, when it is known. */
export function modeBadge(connector: Pick<PodConnector, "mode" | "sharedAccount" | "status">, owner = ""): string {
    if (connector.mode === "PER_MEMBER") return "Your account";
    if (!connector.sharedAccount) return connector.status === "NEEDS_ACCOUNT" ? "Shared · no account yet" : "Shared";
    return "Shared · " + connector.sharedAccount + (owner ? " · by " + owner : "");
}

/** One of my organization accounts, as far as this page needs it. The shape
 *  `accounts.ts` reads; restated so this file stays free of it. */
export interface MyAccount {
    id: string;
    authConfigId: string;
    connectorId: string;
    label: string;
    status: string;
    usable: boolean;
    isDefault: boolean;
}

/** Where I stand with one connector.
 *
 *  - `connected`: a call runs, as my account or the shared one.
 *  - `connect`: per-member, and I have no account on its installation.
 *  - `reconnect`: the account a call would use is mine and needs signing in.
 *  - `waiting`: shared, and somebody else has to share or reconnect one.
 *  - `off`: the installation is turned off. */
export type MyStanding = "connected" | "connect" | "reconnect" | "waiting" | "off";

export interface MyStatus {
    standing: MyStanding;
    /** The account it runs as for me, when that is mine. */
    account: MyAccount | null;
    /** One line, for beside the badge. */
    note: string;
}

/** My status with a connector, from my own accounts.
 *
 *  A per-member call uses my default account on the connector's installation,
 *  and the server adds it to the pod the first time — so any usable account of
 *  mine on that installation is "connected", whether or not it is in the pod
 *  yet. `preferred` is the account my calls here are pinned to, when one is. */
export function myStatus(connector: PodConnector, mine: MyAccount[], me: string, preferred = ""): MyStatus {
    if (connector.status === "UNAVAILABLE") return { standing: "off", account: null, note: statusNote(connector) };
    if (connector.mode === "SHARED") {
        if (connector.status === "NEEDS_ACCOUNT") return { standing: "waiting", account: null, note: "Nobody has shared an account for it yet" };
        const own = connector.sharedOwnerId !== "" && connector.sharedOwnerId === me;
        const account = own ? mine.find((one) => one.authConfigId === connector.authConfigId && one.status.toUpperCase() === "REAUTH_REQUIRED") ?? null : null;
        if (account) return { standing: "reconnect", account, note: "Your shared account needs signing in again" };
        return { standing: "connected", account: null, note: own ? "Runs as your account, for everyone here" : "Runs as the shared account" };
    }
    const on = mine.filter((one) => one.authConfigId === connector.authConfigId);
    const account = on.find((one) => one.id === preferred) ?? on.find((one) => one.isDefault) ?? on.find((one) => one.usable) ?? on[0] ?? null;
    if (!account) return { standing: "connect", account: null, note: "You have not connected one" };
    if (!account.usable) {
        return account.status.toUpperCase() === "REAUTH_REQUIRED" || account.status.toUpperCase() === "DISCONNECTED"
            ? { standing: "reconnect", account, note: "Needs signing in again" }
            : { standing: "connect", account, note: "Not usable yet" };
    }
    return { standing: "connected", account, note: account.label ? "As " + account.label : "As your account" };
}

/** Whether connecting would let me use this connector.
 *
 *  The server's own `can_use` when it sends one. A server that does not say
 *  is answered from `podMayUse` — whether I hold `connector.use` across the
 *  pod — and only for a connector open to the whole pod: a RESTRICTED one
 *  may leave me out, and asking somebody to connect an account that then
 *  cannot be used is the one thing this must not do. */
export function mayUseOnceConnected(connector: Pick<PodConnector, "canUse" | "visibility">, podMayUse: boolean | null): boolean {
    if (connector.canUse !== null) return connector.canUse;
    return podMayUse === true && connector.visibility === "POD";
}

/** The required per-member connectors that could ask me to connect: ready,
 *  and ones I could use once I had. Before my accounts are read. */
export function askableOfMe(connectors: PodConnector[], podMayUse: boolean | null): PodConnector[] {
    return connectors.filter((one) => one.required && one.mode === "PER_MEMBER" && one.status === "READY" && mayUseOnceConnected(one, podMayUse));
}

/** The required per-member connectors I have no working account for and could
 *  use once I had — what the banner asks me to connect, in the pod's own
 *  order. */
export function missingForMe(connectors: PodConnector[], mine: MyAccount[], me: string, podMayUse: boolean | null = null): { connector: PodConnector; status: MyStatus }[] {
    return askableOfMe(connectors, podMayUse)
        .map((connector) => ({ connector, status: myStatus(connector, mine, me) }))
        .filter((one) => one.status.standing === "connect" || one.status.standing === "reconnect");
}

/** What to call a pod connector to a person: its app's name when the slug is
 *  just that app (`gmail`), and its own name otherwise -- three connectors
 *  on one REST API are three different things, and "OpenAPI" says none. */
export function connectorCalled(connector: PodConnector, appTitle: string): string {
    const slug = connector.slug;
    const app = connector.connectorId;
    if (appTitle && (slug === app || slug.startsWith(app + "-"))) return appTitle;
    return slug || appTitle;
}

/** A connector's name on its row: the slug apps and agents call it by, with
 *  its app's title beside it when that says something the slug does not —
 *  "support-inbox (Gmail)", but just "gmail" for Gmail. */
export function connectorName(slug: string, appTitle: string): string {
    const plain = (value: string) => value.toLowerCase().replace(/[^a-z0-9]+/g, "");
    if (!appTitle || plain(appTitle) === plain(slug)) return slug;
    return slug + " (" + appTitle + ")";
}

/** The banner's sentence: "This pod uses Gmail — connect yours". `names`
 *  turns a catalog id into its title. */
export function bannerLine(missing: { connector: PodConnector; status: MyStatus }[], names: (connectorId: string) => string, teammate: string): string {
    if (missing.length === 0) return "";
    const said = [...new Set(missing.map((one) => connectorCalled(one.connector, names(one.connector.connectorId))))];
    const what = said.length === 1 ? said[0] : said.slice(0, -1).join(", ") + " and " + said[said.length - 1];
    const again = missing.every((one) => one.status.standing === "reconnect");
    return teammate + " uses " + what + " — " + (again ? "sign in again" : "connect yours");
}

/* ── writing one ────────────────────────────────────────────────────── */

/** A connector to declare: an installation of the organization's, under a
 *  slug, with whose account it runs as. */
export interface PodConnectorDraft {
    slug: string;
    authConfigId: string;
    mode: PodConnectorMode;
    /** SHARED: the pod account it runs as — one its owner already shared.
     *  Empty declares it with none, and it waits for one. */
    sharedPodAccountId: string;
    required: boolean;
    purpose: string;
}

/** A change to one. Absent fields are left as they are; an empty
 *  `sharedPodAccountId` takes the account off it. */
export interface PodConnectorPatch {
    mode?: PodConnectorMode;
    sharedPodAccountId?: string;
    required?: boolean;
    purpose?: string;
}

export function connectorCreateBody(draft: PodConnectorDraft): Record<string, unknown> {
    const shared = draft.mode === "SHARED" && draft.sharedPodAccountId;
    return {
        slug: draft.slug,
        auth_config_id: draft.authConfigId,
        mode: draft.mode,
        ...(shared ? { shared_pod_account_id: draft.sharedPodAccountId } : {}),
        /* Asking every member to connect means something only when each brings
           their own. */
        ...(draft.mode === "PER_MEMBER" && draft.required ? { required: true } : {}),
        ...(draft.purpose.trim() ? { purpose: draft.purpose.trim() } : {}),
    };
}

export function connectorPatchBody(patch: PodConnectorPatch): Record<string, unknown> {
    const body: Record<string, unknown> = {};
    if (patch.mode) body.mode = patch.mode;
    if (patch.sharedPodAccountId !== undefined) body.shared_pod_account_id = patch.sharedPodAccountId || null;
    if (patch.required !== undefined) body.required = patch.required;
    if (patch.purpose !== undefined) body.purpose = patch.purpose.trim();
    return body;
}

/** The change between a connector and the edits to it, field by field, so a
 *  save never writes back what nobody touched. */
export function connectorChanges(before: PodConnector, after: PodConnectorPatch): PodConnectorPatch {
    const patch: PodConnectorPatch = {};
    if (after.mode && after.mode !== before.mode) patch.mode = after.mode;
    const mode = after.mode ?? before.mode;
    if (after.sharedPodAccountId !== undefined && after.sharedPodAccountId !== before.sharedPodAccountId) patch.sharedPodAccountId = after.sharedPodAccountId;
    /* Moving to per-member leaves no account to run as. */
    if (mode === "PER_MEMBER" && before.sharedPodAccountId && patch.mode) patch.sharedPodAccountId = "";
    if (after.required !== undefined && after.required !== before.required) patch.required = after.required;
    if (after.purpose !== undefined && after.purpose.trim() !== before.purpose) patch.purpose = after.purpose;
    return patch;
}
