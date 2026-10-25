import { isCredentialConflict } from "@web/data/accounts";
import { connectorsFor, type PodConnector } from "@web/data/pod-connectors";
import { connectorProblem } from "@web/connect/install";
import { fields } from "@web/connect/schema";
import { firstFew, isForbidden } from "../words";
import type { Answerer, GuidedSetup, MySurface, Surface } from "./types";
import { unavailableNote, type Connectable } from "@web/data/connectable";
import { isGroupPlatform, type Answering } from "@web/channels/sources";

/** Where a Lemma is reached, read and said: which places it answers, which
 *  it could, what each would take, and the words for all of it. Pure, so
 *  `tests/admin-reach.test.ts` can hold it.
 *
 *  The web's `shell/reach.tsx`, `surface-manage.tsx` and `channels.tsx`
 *  decide these inside their components; here they are functions, said in
 *  the phone's words. A place is never called a "channel" on screen: it is
 *  WhatsApp, a number, an address, a group. */

/** Email has four platform names on the wire and is one place to a person. */
export function placeKey(platform: string): string {
    const key = platform.toUpperCase();
    return ["EMAIL", "RESEND", "GMAIL", "OUTLOOK"].includes(key) ? "EMAIL" : key;
}

const NAMES: Record<string, string> = {
    WHATSAPP: "WhatsApp",
    TELEGRAM: "Telegram",
    EMAIL: "Email",
    SLACK: "Slack",
    TEAMS: "Microsoft Teams",
    DISCORD: "Discord",
};

/** What a person calls the platform. One the build does not know keeps the
 *  server's word for it, said as a name rather than shouted. */
export function placeName(platform: string): string {
    const key = placeKey(platform);
    return NAMES[key] ?? (key ? key.charAt(0) + key.slice(1).toLowerCase() : "Somewhere");
}

export type PlaceGlyph = "whatsapp" | "telegram" | "mail" | "slack" | "teams" | "discord" | "other";

export function placeGlyph(platform: string): PlaceGlyph {
    switch (placeKey(platform)) {
        case "WHATSAPP": return "whatsapp";
        case "TELEGRAM": return "telegram";
        case "EMAIL": return "mail";
        case "SLACK": return "slack";
        case "TEAMS": return "teams";
        case "DISCORD": return "discord";
        default: return "other";
    }
}

/** One of a few, as `ui.tsx`'s `ChoiceRow` takes it (said again here so
 *  this module stays free of React Native). */
export interface Choice<T extends string = string> {
    value: T;
    label: string;
    hint?: string | null;
}

/* ── a connected place ──────────────────────────────────────────────── */

/** Where people write to it, as they would type it: an address without a
 *  stray `mailto:` or an escaped `@`, a number, a username. */
export function addressOf(surface: Pick<Surface, "platform" | "handle" | "email">): string {
    if (placeKey(surface.platform) === "EMAIL") {
        return (surface.email || surface.handle).trim().replace(/^(mailto:)+/i, "").replace(/\\@/g, "@");
    }
    return surface.handle.trim();
}

/** What the address is, for the line over it. */
export function addressLabel(platform: string): string {
    switch (placeKey(platform)) {
        case "WHATSAPP": return "Its number";
        case "EMAIL": return "Its address";
        case "TELEGRAM": return "Its username";
        default: return "Its name there";
    }
}

/** Where a person goes to say something to it (the web's `sayHi`): a
 *  Telegram chat, a WhatsApp chat, a new email. Null where the platform has
 *  no link to a conversation. */
export function sayHi(surface: Pick<Surface, "platform" | "handle" | "email">): string | null {
    const key = placeKey(surface.platform);
    const handle = surface.handle.trim();
    if (!handle) return null;
    if (key === "TELEGRAM") return "https://t.me/" + handle.replace(/^@/, "");
    if (key === "WHATSAPP") return "https://wa.me/" + handle.replace(/[^\d]/g, "");
    if (key === "EMAIL") return "mailto:" + addressOf(surface);
    return null;
}

/** The words on the way to `sayHi`. */
export function sayHiLabel(platform: string): string {
    const key = placeKey(platform);
    return key === "EMAIL" ? "Write to it" : "Say hi in " + placeName(platform);
}

/** One line for a connected place on the list: its address, then whether it
 *  works (the web's `surfaceStatus`). */
export function placeStatus(status: string | undefined, active: boolean): string {
    switch (status) {
        case "PENDING_ADMIN_CONSENT": return "Needs administrator approval";
        case "NEEDS_SETUP": return "Finish setting it up";
        case "ERROR": return "Needs attention";
        case "INACTIVE": return "Paused";
        default: return active ? "Connected" : "Needs attention";
    }
}

/** Whether a place waits on somebody: approving it, or finishing it. */
export function placeNeedsYou(surface: Pick<Surface, "status" | "active">): boolean {
    return surface.status === "PENDING_ADMIN_CONSENT" || surface.status === "NEEDS_SETUP" || surface.status === "ERROR"
        || (!surface.active && surface.status !== "INACTIVE");
}

/** The places that answer as the Lemma itself. The ones bound to one of its
 *  agents belong to that agent (the web's `surfacesForAgent`). */
export function ownPlaces(surfaces: readonly Surface[]): Surface[] {
    return surfaces.filter((surface) => surface.mine);
}

/** The line on a Lemma's Settings tab (the web's About line): the places it
 *  is reached on now, else that it is reached only in Lemma. */
export function reachLine(surfaces: readonly Surface[]): string {
    const on = [...new Set(surfaces.filter((one) => one.active).map((one) => placeName(one.platform)))];
    return on.length ? firstFew(on, 3) : "Only here, in Lemma";
}

/** The note under the screen's heading when the list is read: how many
 *  places, or only here. */
export function reachCount(name: string, count: number): string {
    if (count === 0) return `${name} can only be reached here, in Lemma.`;
    return `${name} answers in ${count === 1 ? "one place" : `${count} places`} besides Lemma.`;
}

/* ── what connecting costs ──────────────────────────────────────────── */

/** Platforms where a Lemma can be given an app of its own (one Slack app is
 *  one bot user). */
export const OWN_APP = new Set(["SLACK"]);

/** What this will cost, in words, before anything is pressed. */
export const COST: Record<Connectable["effort"], string> = {
    instant: "Ready now",
    guided: "About a minute",
    account: "Needs your account",
    unavailable: "Not available",
};

/** The cost line, where the general one would mislead: a Telegram bot of
 *  one's own is a token pasted, not an account signed into. */
export function costOf(entry: Connectable): string {
    if (entry.effort === "account" && entry.platform === "TELEGRAM" && fields(entry.credentialSchema).length > 0) return "Needs a bot token";
    return COST[entry.effort];
}

/** What the place is, said to somebody deciding where a Lemma answers. The
 *  catalog's own description is connector documentation, kept only for a
 *  platform this build has never heard of. */
const BLURB: Record<string, string> = {
    TELEGRAM: "People message it directly, like anyone else on Telegram.",
    WHATSAPP: "On the app people already message from.",
    RESEND: "An address of its own. Write to it like a colleague.",
    EMAIL: "An address of its own. Write to it like a colleague.",
    SLACK: "Where your team already talks.",
    TEAMS: "For teams that live in Microsoft 365.",
    DISCORD: "In the servers your community already uses.",
};

export function blurbOf(entry: Connectable): string {
    return BLURB[entry.platform] ?? entry.description;
}

/** Why this place costs what it costs (the web's `why`). The sentence that
 *  matters most is the shared identity somebody else in the organization
 *  already took. */
export function why(entry: Connectable): string {
    if (entry.system && !entry.systemFree) {
        return "Lemma's shared " + (entry.platform === "WHATSAPP" ? "number" : "identity") + " is already taken elsewhere in this organization.";
    }
    if (entry.effort === "instant" && entry.platform === "RESEND" && entry.emailDomain) return "An address of its own on " + entry.emailDomain + ".";
    return blurbOf(entry);
}

/** The places not connected yet: everything the catalog offers, less what
 *  already answers here. Nothing until the connected ones are read, so a
 *  place is never offered twice. */
export function offerOf(catalog: readonly Connectable[], on: readonly Surface[], read: boolean): Connectable[] {
    if (!read) return [];
    const already = new Set(on.map((surface) => placeKey(surface.platform)));
    return catalog.filter((entry) => !already.has(placeKey(entry.platform)));
}

/** The platforms another Lemma in the organization, or another of this
 *  Lemma's agents, already answers on: the only way to know that an account
 *  the organization holds is likely spoken for. */
export function heldElsewhere(mine: readonly MySurface[], podId: string, here: readonly Surface[]): Set<string> {
    return new Set(
        mine.filter((surface) => surface.podId !== podId).map((surface) => placeKey(surface.platform))
            .concat(here.filter((surface) => !surface.mine).map((surface) => placeKey(surface.platform))),
    );
}

/** What a place not connected yet says and does on the list (the web's
 *  `ConnectRow`, decided once). */
export interface ConnectPlan {
    name: string;
    /** The cost, said at the end of the row. */
    cost: string;
    /** Why, or what stops it. */
    note: string;
    /** "Using …, already shared in this Lemma." */
    using: string | null;
    /** Said when the shared account here is likely another Lemma's. */
    inUse: string | null;
    /** `connect`: Lemma's own identity, now. `use-account`: a SHARED
     *  connector the Lemma already has, now. `set-up`: a screen of steps.
     *  `own`: an identity of its own, because the shared one is taken.
     *  `blocked`: nothing can connect here. */
    action: "connect" | "use-account" | "set-up" | "own" | "blocked";
    /** The connector `use-account` binds, by slug, and what to call it. */
    podConnector: string | null;
    account: string | null;
    /** A way in with an account, number or bot of its own is offered too. */
    ownWay: boolean;
}

/** A shared connector by its slug, and whose account it answers as. */
function heldName(connector: PodConnector): string {
    return connector.slug + (connector.sharedAccount ? " (" + connector.sharedAccount + ")" : "");
}

export function connectPlan(entry: Connectable, connectors: readonly PodConnector[], elsewhere: boolean): ConnectPlan {
    const name = entry.title || placeName(entry.platform);
    /* A place answers as one account for everyone, so only a SHARED
       connector the Lemma already has can carry it at once. */
    const held = entry.effort === "account" ? connectorsFor([...connectors], entry.connectorId, "SHARED").find((one) => one.status === "READY") ?? null : null;
    /* Not on Slack: an account there is Lemma's app or another Lemma's own. */
    const ready = elsewhere || entry.platform === "SLACK" ? null : held;
    const guiding = entry.effort === "guided";
    const blockedNote = unavailableNote(entry, null);
    const taken = elsewhere && Boolean(held);
    const ownWay = (entry.guided || OWN_APP.has(entry.platform) || fields(entry.credentialSchema).length > 0) && !guiding && !taken && !blockedNote;
    /* A place with no way in here still opens, and says so there: who runs
       the server can add it (the web's "Set up" on such a row). */
    const action: ConnectPlan["action"] = blockedNote ? "blocked"
        : guiding ? "set-up"
        : entry.effort === "instant" ? "connect"
        : ready ? "use-account"
        : taken ? "own"
        : "set-up";
    return {
        name,
        cost: ready ? COST.instant : taken ? "Already in use" : costOf(entry),
        note: blockedNote ?? why(entry),
        using: ready && !guiding ? `Using ${heldName(ready)}, already shared in this Lemma.` : null,
        inUse: taken && !guiding ? `${name} is already in use elsewhere in this organization, so this one needs an identity of its own.` : null,
        action,
        podConnector: ready?.slug ?? null,
        account: ready ? heldName(ready) : null,
        ownWay,
    };
}

/** The one line under a place on the list, short enough for two lines of a
 *  narrow row: what it costs, then why. The whole of it is on the place's
 *  own screen. */
export function listHint(plan: ConnectPlan): string {
    if (plan.action === "connect") return "Ready now";
    if (plan.action === "use-account") return `Ready now, using ${plan.account ?? "the organization's account"}`;
    if (plan.action === "own") return "Already in use elsewhere here. It needs one of its own.";
    return `${plan.cost} · ${plan.note}`;
}

/** What Lemma's own identity on a platform is, said on the place's screen
 *  above its Connect. */
export function systemLine(entry: Connectable, lemma: string): string {
    switch (entry.platform) {
        case "WHATSAPP": return `Lemma's shared WhatsApp number answers as ${lemma}.`;
        case "TELEGRAM": return `Lemma's own Telegram bot answers as ${lemma}.`;
        case "RESEND": return entry.emailDomain ? `An address of its own on ${entry.emailDomain}, made now.` : "An address of its own, made now.";
        default: return `Lemma's own ${placeName(entry.platform)} identity answers as ${lemma}.`;
    }
}

/** A refused connection, said usefully: an account another Lemma answers
 *  on, a person who may not, the connector's own reasons (`connectorProblem`). */
export function connectProblem(error: unknown, fallback: string): string {
    if (isCredentialConflict(error)) return "That account already answers as another Lemma in this organization.";
    if (isForbidden(error)) return "You may not change where this Lemma is reached.";
    return connectorProblem(error, fallback);
}

/** Which way in a place's own screen leads with (the web's `Focused`). */
export type ConnectWay = "credentials" | "own-app" | "slack-manager" | "guided" | "account" | "none";

export function connectWay(entry: Connectable, { custom, ownApp }: { custom: boolean; ownApp: boolean }): ConnectWay {
    const credentials = fields(entry.credentialSchema).length > 0;
    if ((custom || !entry.guided) && credentials) return "credentials";
    if (ownApp && OWN_APP.has(entry.platform)) return "own-app";
    if (entry.platform === "SLACK" && entry.account) return "slack-manager";
    if (entry.guided) return "guided";
    return entry.account ? "account" : "none";
}

/** Whether the place's screen leads with a one-tap way in before the steps:
 *  Lemma's own identity, or the account the organization already holds. */
export function quickWay(plan: ConnectPlan): boolean {
    return plan.action === "connect" || plan.action === "use-account";
}

/* ── setting it up ──────────────────────────────────────────────────── */

/** How often a setup that finishes somewhere else is asked about, how many
 *  failed asks in a row end the asking, and how long an unfinished sign-in
 *  is waited for (the web's numbers). */
export const POLL_EVERY_MS = 2500;
export const GIVE_UP_AFTER_FAILURES = 8;
export const ACCOUNT_WAIT_MS = 15 * 60_000;

/** Where a Telegram setup stands. */
export function guidedState(setup: GuidedSetup | null, now = Date.now()): "none" | "waiting" | "ready" | "failed" {
    if (!setup) return "none";
    if (setup.status === "COMPLETE" || setup.status === "READY") return "ready";
    if (setup.status === "FAILED") return "failed";
    if (setup.expiresAt && Date.parse(setup.expiresAt) < now) return "failed";
    return "waiting";
}

/** Whether it works, in one line (the web's `Health`). */
export function healthOf(setup: { ready?: boolean; status?: string } | undefined, pending: boolean, failed: boolean): { text: string; tone: "quiet" | "waiting" | "good" } {
    if (pending) return { text: "Checking it works…", tone: "quiet" };
    if (failed || !setup) return { text: "Couldn't check whether it works", tone: "quiet" };
    if (!setup.ready) return { text: "Not finished yet. The steps left are below.", tone: "waiting" };
    if (setup.status === "INACTIVE") return { text: "Connected, and paused", tone: "quiet" };
    return { text: "Connected and working", tone: "good" };
}

/** What turning it off pauses, on each platform. */
export function pauses(platform: string): string {
    switch (placeKey(platform)) {
        case "WHATSAPP": return "Off pauses every chat and group on this number.";
        case "TELEGRAM": return "Off pauses every chat and group it is in.";
        case "SLACK":
        case "TEAMS": return "Off pauses everything there, direct messages too.";
        default: return "Off pauses everything that arrives here.";
    }
}

/** Who answers a place: the Lemma, or one of its agents. An agent the list
 *  no longer has is kept, so opening the screen never changes it. */
export function answererChoices(podName: string, agents: readonly Answerer[], held: string): Choice[] {
    const others = agents.filter((agent) => !agent.front);
    const choices: Choice[] = [{ value: "pod_default", label: podName }];
    if (held !== "pod_default" && !others.some((agent) => agent.name === held)) choices.push({ value: held, label: held });
    return choices.concat(others.map((agent) => ({ value: agent.name, label: agent.label })));
}

export function answererName(held: string, podName: string, agents: readonly Answerer[] | undefined, fallback: string): string {
    if (held === "pod_default") return podName;
    return agents?.find((agent) => agent.name === held)?.label ?? fallback;
}

/** Whom a place answers in a private chat beyond the Lemma's people. */
export function answerChoices(space: string): Choice<"off" | "known" | "anyone">[] {
    return [
        { value: "off", label: "Members only", hint: `Anyone outside ${space} is turned away.` },
        { value: "known", label: "Members and contacts", hint: "People already in Contacts are answered too. Strangers are turned away." },
        { value: "anyone", label: "Everyone", hint: "Strangers are answered too, and join Contacts with their first message." },
    ];
}

/** What the Slack or Teams list of places is called. */
export function routesHeading(platform: string): string {
    return `Where in ${placeName(platform)} it answers`;
}

/* ── groups ─────────────────────────────────────────────────────────── */

export function groupsHeading(platform: string, bot: string): string {
    const key = placeKey(platform);
    if (key === "WHATSAPP") return "Groups on this number";
    if (key === "SLACK") return "Groups in this workspace";
    return `Groups ${bot} is in`;
}

/** The way to bring it into another group, on each platform. */
export function addGroupLabel(platform: string, bot: string): string {
    const key = placeKey(platform);
    if (key === "WHATSAPP") return "Start a WhatsApp group";
    if (key === "SLACK") return `Add ${bot} in Slack`;
    return `Add ${bot} to a Telegram group`;
}

/** What an empty list of groups says: how one gets there. */
export function noGroupsYet(platform: string, bot: string): string {
    switch (placeKey(platform)) {
        case "TELEGRAM": return `Add ${bot} to a Telegram group and it shows up here.`;
        case "WHATSAPP": return `Start a WhatsApp group on ${bot}'s number and it shows up here.`;
        case "SLACK": return `Invite ${bot} in Slack and it shows up here after the next message there.`;
        default: return "None yet.";
    }
}

/** Who answers a group's people from outside the Lemma, as one line under
 *  the group (the web's `sayAnsweringFully`, in the phone's words). */
export function groupLine(state: Answering, space: string): string {
    switch (state.kind) {
        case "pending": return "Being created";
        case "invited": return `People outside ${space} are invited to join`;
        case "bot-off": return `Answering people outside ${space} is off here`;
        case "off": return `People outside ${space} are not answered`;
        case "nobody": return `Nobody answers people outside ${space}`;
        case "you": return `You answer for people outside ${space}`;
        case "someone": return `${state.name ?? "Someone in " + space} answers for people outside ${space}`;
    }
}

/** Why starting a WhatsApp group failed, in words. */
export function startGroupProblem(error: unknown, bot: string): string {
    const status = (error as { statusCode?: unknown } | null)?.statusCode;
    const said = error instanceof Error ? error.message.trim() : "";
    if (status === 502) return "WhatsApp would not start the group. Try again shortly.";
    if (isForbidden(error)) return `Only someone who can change ${bot} can start a group on this number.`;
    if ((status === 409 || status === 422) && said) return said;
    return "That didn't work. Try again.";
}

/** Why a Telegram link could not be made. */
export function linkProblem(error: unknown, bot: string): string {
    const status = (error as { statusCode?: unknown } | null)?.statusCode;
    const said = error instanceof Error ? error.message.trim() : "";
    if (isForbidden(error)) return `Only someone who can change ${bot} can add it to a group.`;
    if ((status === 409 || status === 422) && said) return said.replace(/\.?$/, ".");
    return "Couldn't make the link. Try again.";
}

export function hasGroups(platform: string): boolean {
    return isGroupPlatform(platform);
}

/** The first group that was not there before: how a sheet waiting on
 *  Telegram or Slack knows the group has arrived. */
export function arrivedGroup<T extends { id: string }>(before: ReadonlySet<string> | null, groups: readonly T[]): T | null {
    if (!before) return null;
    return groups.find((group) => !before.has(group.id)) ?? null;
}

/* ── Slack ──────────────────────────────────────────────────────────── */

export const SLACK_APPS = "https://api.slack.com/apps";

/** Slack's own page for making an app from a manifest, with this one in it:
 *  the manifest opened rather than pasted. */
export function slackManifestLink(manifest: Record<string, unknown>): string {
    return SLACK_APPS + "?new_app=1&manifest_json=" + encodeURIComponent(JSON.stringify(manifest));
}

export function workspaceName(workspace: { teamId: string; teamName: string | null }): string {
    return workspace.teamName || workspace.teamId;
}
