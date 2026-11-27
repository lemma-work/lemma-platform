/** A Lemma's connections, judged for whoever is reading:
 *  `GET /pods/{pod_id}/connections` (docs/design/github-app-connector-mode.md,
 *  section B).
 *
 *  One row per app the Lemma uses, each saying whose account a call runs as
 *  and where I stand with it: ready, or the one thing that would make it
 *  ready (connect, sign in again, use an account I already have) or why I
 *  can't (waiting on someone, not allowed). It replaces the page joining the
 *  pod's connectors, its accounts, my organization accounts and coverage
 *  itself, which is how its two lists came to disagree.
 *
 *  Pure, like `pod-connectors.ts`: every field read here is a wire field, and
 *  anything this client does not know reads as not ready, never as working.
 */

import type { PodConnectorMode, PodConnectorStatus } from "./pod-connectors";

/** Where I stand with one app here. `unknown` is a standing this client was
 *  not taught: shown as not working for me, with nothing offered. */
export type ConnectionStanding = "ready" | "connect" | "reconnect" | "use_existing" | "install" | "waiting" | "not_allowed" | "unknown";

/** Why a GitHub account here reaches no installation of the App, by who can
 *  fix it (`me.github` with `install`). `unknown` is a reason this client was
 *  not taught: still not ready, with nothing but checking again offered. */
export type GithubWait = "install" | "choose" | "github_admin" | "github_permission" | "lemma_admin" | "check" | "unknown";

/** An installation of the App the account can see. */
export interface GithubOffer {
    installationId: string;
    /** The GitHub account it is on. */
    login: string;
    /** Its settings page on GitHub. */
    manageUrl: string;
}

export interface GithubStanding {
    wait: GithubWait;
    /** The GitHub account the installation in question is on. */
    login: string;
    manageUrl: string;
    /** With `install`: where the App is installed. */
    installUrl: string;
    /** With `choose`, the ones to pick from. */
    choices: GithubOffer[];
}

/** Who a call by me runs as. */
export type RunsAsKind = "me" | "person" | "app" | "nobody";

export interface RunsAs {
    kind: RunsAsKind;
    /** My account's label, the sharer's account, or the bot's GitHub
     *  organization login(s). */
    label: string;
    /** SHARED: the person who shared the account. */
    ownerUserId: string;
    ownerName: string;
}

/** My account on this app in the Lemma. */
export interface MyConnectionAccount {
    /** The organization account. */
    id: string;
    label: string;
    /** The pod account: what removing it here, or sharing it, names. */
    podAccountId: string;
}

/** `use_existing`: an account of mine in the organization on this app's
 *  installation, not in the Lemma yet. */
export interface ExistingAccount {
    accountId: string;
    label: string;
}

export interface PodConnection {
    /** What apps and agents call it by. Shown only when the Lemma has two of
     *  one app. */
    slug: string;
    /** The catalog app: `github`, `slack`. */
    connectorId: string;
    title: string;
    icon: string;
    mode: PodConnectorMode;
    purpose: string;
    required: boolean;
    status: PodConnectorStatus;
    runsAs: RunsAs;
    me: { standing: ConnectionStanding; account: MyConnectionAccount | null; existing: ExistingAccount | null; github: GithubStanding | null };
    /** PER_MEMBER, for someone who may manage the Lemma's connectors. */
    coverage: { connected: number; total: number } | null;
    /** The installation it runs through, which a sign-in is started against.
     *  Not in section B's sketch: read when the server sends it, and empty
     *  otherwise (the connect request then names the app, and the server
     *  picks the organization's default installation). */
    authConfigId: string;
    installationName: string;
}

/** An installation of the organization's GitHub App that a bot connector can
 *  act through. */
export interface BotInstallation {
    login: string;
    /** `all` or `selected`, as GitHub says it. */
    repositorySelection: string;
}

export interface PodConnections {
    /** `pod.connector.manage`: add, change and remove the Lemma's apps. */
    mayManage: boolean;
    /** The organization's GitHub App, where it has an installation a bot
     *  connector may use. Null when it has none. */
    githubBot: { installations: BotInstallation[] } | null;
    items: PodConnection[];
}

const MODES: PodConnectorMode[] = ["PER_MEMBER", "SHARED", "APP"];
const STATUSES: PodConnectorStatus[] = ["READY", "NEEDS_ACCOUNT", "NEEDS_INSTALLATION", "UNAVAILABLE"];
const STANDINGS: ConnectionStanding[] = ["ready", "connect", "reconnect", "use_existing", "install", "waiting", "not_allowed"];
const WAITS: GithubWait[] = ["install", "choose", "github_admin", "github_permission", "lemma_admin", "check"];
const RUNS_AS: RunsAsKind[] = ["me", "person", "app", "nobody"];

function record(value: unknown): Record<string, unknown> {
    return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function text(value: unknown): string {
    return typeof value === "string" ? value.trim() : "";
}

function count(value: unknown): number {
    return typeof value === "number" && Number.isFinite(value) && value >= 0 ? Math.floor(value) : 0;
}

function readAccount(raw: unknown): MyConnectionAccount | null {
    const row = record(raw);
    const id = text(row["id"]);
    const podAccountId = text(row["pod_account_id"]);
    if (!id && !podAccountId) return null;
    return { id, label: text(row["label"]), podAccountId };
}

function readExisting(raw: unknown): ExistingAccount | null {
    const row = record(raw);
    const accountId = text(row["account_id"]);
    return accountId ? { accountId, label: text(row["label"]) } : null;
}

/** Only an https link to GitHub is offered as one. */
function githubLink(value: unknown): string {
    const url = text(value);
    return /^https:\/\/github\.com\//.test(url) ? url : "";
}

function readGithub(raw: unknown): GithubStanding | null {
    if (!raw || typeof raw !== "object") return null;
    const row = record(raw);
    const wait = text(row["wait"]).toLowerCase() as GithubWait;
    return {
        wait: WAITS.includes(wait) ? wait : "unknown",
        login: text(row["login"]),
        manageUrl: githubLink(row["manage_url"]),
        installUrl: githubLink(row["install_url"]),
        choices: (Array.isArray(row["choices"]) ? row["choices"] as unknown[] : [])
            .map((entry) => {
                const one = record(entry);
                return { installationId: text(one["installation_id"]), login: text(one["login"]), manageUrl: githubLink(one["manage_url"]) };
            })
            .filter((one) => one.installationId),
    };
}

export function readPodConnection(raw: unknown): PodConnection | null {
    const row = record(raw);
    const slug = text(row["slug"]);
    if (!slug) return null;
    const mode = text(row["mode"]).toUpperCase() as PodConnectorMode;
    const status = text(row["status"]).toUpperCase() as PodConnectorStatus;
    const runs = record(row["runs_as"]);
    const kind = text(runs["kind"]).toLowerCase() as RunsAsKind;
    const me = record(row["me"]);
    const standing = text(me["standing"]).toLowerCase() as ConnectionStanding;
    const coverage = row["coverage"] && typeof row["coverage"] === "object" ? record(row["coverage"]) : null;
    const connectorId = text(row["connector_id"]);
    /* A status this client does not know is not one to offer as ready; a
       standing it does not know is not one to act on. */
    const known = STATUSES.includes(status) ? status : "UNAVAILABLE";
    const said = STANDINGS.includes(standing) ? standing : "unknown";
    return {
        slug,
        connectorId,
        title: text(row["title"]) || connectorId || slug,
        icon: text(row["icon"]),
        mode: MODES.includes(mode) ? mode : "PER_MEMBER",
        purpose: text(row["purpose"]),
        required: row["required"] === true,
        status: known,
        runsAs: {
            kind: RUNS_AS.includes(kind) ? kind : "nobody",
            label: text(runs["label"]),
            ownerUserId: text(runs["owner_user_id"]),
            ownerName: text(runs["owner_name"]),
        },
        me: {
            /* "Ready" on a row the server says cannot run is a contradiction;
               the row believes the row. */
            standing: said === "ready" && known !== "READY" ? "waiting" : said,
            account: readAccount(me["account"]),
            existing: readExisting(me["existing"]),
            github: said === "install" ? readGithub(me["github"]) : null,
        },
        coverage: coverage ? { connected: count(coverage["connected"]), total: count(coverage["total"]) } : null,
        authConfigId: text(row["auth_config_id"]),
        installationName: text(row["installation_name"]),
    };
}

export function readPodConnections(raw: unknown): PodConnections {
    const body = record(raw);
    const items = Array.isArray(raw) ? raw : body["items"];
    const bot = body["github_bot"] && typeof body["github_bot"] === "object" ? record(body["github_bot"]) : null;
    const installations = (Array.isArray(bot?.["installations"]) ? bot["installations"] as unknown[] : [])
        .map((entry) => {
            const one = record(entry);
            return { login: text(one["login"]), repositorySelection: text(one["repository_selection"]).toLowerCase() };
        })
        .filter((one) => one.login);
    return {
        mayManage: body["may_manage"] === true,
        githubBot: bot && installations.length ? { installations } : null,
        items: (Array.isArray(items) ? items : []).map(readPodConnection).filter((one): one is PodConnection => one !== null),
    };
}

/* ── said for the person reading ─────────────────────────────────────── */

/** Only GitHub has a bot mode in this round. */
export function hasBotMode(connectorId: string): boolean {
    return connectorId === "github";
}

/** The apps the Lemma has more than one connector of: only there is a
 *  connector's slug worth showing, to tell them apart. */
export function twiceOver(items: readonly Pick<PodConnection, "connectorId">[]): Set<string> {
    const seen = new Map<string, number>();
    for (const one of items) seen.set(one.connectorId, (seen.get(one.connectorId) ?? 0) + 1);
    return new Set([...seen].filter(([, n]) => n > 1).map(([id]) => id));
}

/** "lemma-platform", "lemma-platform and acme". */
export function botLogins(bot: PodConnections["githubBot"]): string {
    const logins = (bot?.installations ?? []).map((one) => one.login);
    return logins.length < 2 ? logins.join("") : logins.slice(0, -1).join(", ") + " and " + logins[logins.length - 1];
}

/** What the bot reaches, as GitHub put it. */
export function botReach(bot: PodConnections["githubBot"]): string {
    const all = (bot?.installations ?? []).every((one) => one.repositorySelection === "all");
    return all ? "Reaches every repository the App was given." : "Reaches the repositories the App was given.";
}

export type Tone = "ok" | "warn" | "bad" | "off";

/** The status beside a row: a dot and a word (kit `.kstatus`). */
export interface RowStatus { word: string; tone: Tone }

/** A row's line under its name and its status: whose account it runs as for
 *  me, or why it can't run, and who can fix it. `lemma` is the Lemma's
 *  name; `me` my user id, to tell my shared account from somebody else's. */
export function connectionSaid(item: PodConnection, lemma: string, me: string): { line: string; status: RowStatus } {
    const app = item.title;
    const owner = item.runsAs.ownerUserId && item.runsAs.ownerUserId === me ? "you" : item.runsAs.ownerName;
    if (item.status === "UNAVAILABLE") {
        return { line: "Your organization switched " + app + " off. An organization owner can turn it back on.", status: { word: "Switched off", tone: "off" } };
    }
    if (item.me.standing === "not_allowed") {
        return { line: lemma + " uses " + app + ", but you aren't allowed to use it here. Someone who manages " + lemma + " can change that.", status: { word: "Not for you", tone: "off" } };
    }
    if (item.mode === "APP") {
        const logins = item.runsAs.label;
        if (item.status === "NEEDS_INSTALLATION" || item.me.standing !== "ready") {
            return {
                line: "The GitHub App isn't installed on a GitHub organization of yours. " + lemma + " can't use " + app + " until someone who manages the organization's apps installs it.",
                status: { word: "App not installed", tone: "bad" },
            };
        }
        return {
            line: "Runs as " + lemma + "'s bot" + (logins ? " · " + logins + "'s GitHub App" : "") + ". Nobody needs to connect.",
            status: { word: "Working", tone: "ok" },
        };
    }
    if (item.mode === "SHARED") {
        if (item.status !== "READY" || item.me.standing === "waiting" || item.runsAs.kind === "nobody") {
            return {
                line: (owner && owner !== "you" ? "Ran as " + owner + "'s account, which isn't shared any more. " : "")
                    + "Nothing in " + app + " runs until someone shares an account for everyone.",
                status: { word: owner && owner !== "you" ? "Waiting on " + owner : "Waiting for an account", tone: "warn" },
            };
        }
        if (item.me.standing === "install") {
            const why = githubSaid(item.me.github, lemma);
            return { line: (owner === "you" ? "Your account, for everyone. " : owner ? owner + "'s account, for everyone. " : "") + why.line, status: why.status };
        }
        const whose = owner === "you" ? "Your account, for everyone" : owner ? owner + "'s account, for everyone" : "One account, for everyone";
        return { line: whose + (item.runsAs.label ? " · " + item.runsAs.label : ""), status: { word: "Working", tone: "ok" } };
    }
    /* Each person's own. */
    const purpose = item.purpose ? " " + sentence(item.purpose) : "";
    switch (item.me.standing) {
        case "ready": {
            const label = item.me.account?.label || item.runsAs.label;
            return { line: "Runs as you" + (label ? " · " + label : ""), status: { word: "Connected", tone: "ok" } };
        }
        case "use_existing":
            return {
                line: "Runs as each person's own account." + (item.me.existing?.label ? " Yours is " + item.me.existing.label + ", no sign-in needed." : " Yours is connected already, no sign-in needed."),
                status: { word: "Not here yet", tone: "off" },
            };
        case "reconnect":
            return { line: app + " signed you out. " + lemma + " can't use it for you until you sign in again.", status: { word: "Signed out", tone: "bad" } };
        case "install":
            return githubSaid(item.me.github, lemma);
        case "connect":
            return { line: "Runs as each person's own account." + purpose, status: { word: "Not connected", tone: "off" } };
        default:
            return { line: "It isn't working for you here yet.", status: { word: "Not ready", tone: "off" } };
    }
}

/** Why GitHub can't reach anything here yet, said by who can fix it. */
export function githubSaid(github: GithubStanding | null, lemma: string): { line: string; status: RowStatus } {
    const on = github?.login || "that GitHub organization";
    switch (github?.wait) {
        case "install":
            return {
                line: "Your GitHub account is connected, but Lemma's GitHub App isn't installed on anything it can reach. Install it on the GitHub organization " + lemma + " works in.",
                status: { word: "App not installed", tone: "bad" },
            };
        case "choose":
            if (github.choices.length === 1) {
                const one = github.choices[0].login || "a GitHub account";
                return {
                    line: "Lemma's GitHub App is installed on " + one + ", and your organization here stopped using it. Use it again to reach its repositories.",
                    status: { word: "Not in use", tone: "warn" },
                };
            }
            return {
                line: "Lemma's GitHub App is installed on " + github.choices.length + " GitHub accounts you can reach. Pick the one " + lemma + " works in.",
                status: { word: "Pick one", tone: "warn" },
            };
        case "github_admin":
            return {
                line: "Lemma's GitHub App is installed on " + on + ", but only an admin of " + on + " on GitHub can add it to your organization here. Once one has, it works for everyone.",
                status: { word: "Waiting on a GitHub admin", tone: "warn" },
            };
        case "github_permission":
            return {
                line: "An owner of " + on + " has to accept the GitHub App's new Members permission on GitHub. Until then GitHub won't say who its admins are, so nobody can add it here.",
                status: { word: "Waiting on GitHub", tone: "warn" },
            };
        case "lemma_admin":
            return {
                line: "You're an admin of " + on + " on GitHub, but adding it to your organization here takes someone who manages its apps. Once they connect GitHub, it works for everyone.",
                status: { word: "Waiting on an owner", tone: "warn" },
            };
        case "check":
            return {
                line: "GitHub didn't answer in time to say what your account can reach. Check again in a moment.",
                status: { word: "GitHub didn't answer", tone: "off" },
            };
        default:
            return { line: "Your GitHub account is connected, but it can't reach any repository here yet.", status: { word: "Not ready", tone: "off" } };
    }
}

function sentence(words: string): string {
    const said = words.trim();
    return /[.!?]$/.test(said) ? said : said + ".";
}

/** "2 of 3 connected": an editor's count of who has connected a per-member
 *  app. Empty where there is nothing to count. */
export function connectedCount(coverage: PodConnection["coverage"]): string {
    if (!coverage || coverage.total === 0) return "";
    return coverage.connected + " of " + coverage.total + " connected";
}

/** Whether somebody here has not connected a per-member app yet, by the
 *  editor's count. */
export function someoneMissing(item: Pick<PodConnection, "mode" | "coverage" | "status">): boolean {
    return item.mode === "PER_MEMBER" && item.status === "READY" && item.coverage !== null && item.coverage.connected < item.coverage.total;
}

/** The band's sentence for one app whose people haven't all connected:
 *  "Priya hasn't connected GitHub, so Sales can't use it for Priya." */
export function missingLine(names: string[], app: string, lemma: string): string {
    if (names.length === 0) return "";
    const who = names.length === 1 ? names[0] : names.length === 2 ? names.join(" and ") : names.slice(0, 2).join(", ") + " and " + (names.length - 2) + " more";
    const them = names.length === 1 ? names[0] : "them";
    return who + (names.length === 1 ? " hasn't" : " haven't") + " connected " + app + ", so " + lemma + " can't use it for " + them + ".";
}

/** The slug offered for a bot connector: `github` while free, else
 *  `github-bot`, made free (section C's default). */
export function botSlug(taken: readonly string[]): string {
    if (!taken.includes("github")) return "github";
    let slug = "github-bot";
    for (let n = 2; taken.includes(slug); n += 1) slug = "github-bot-" + n;
    return slug;
}
