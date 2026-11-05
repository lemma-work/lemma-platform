import type { PublicReach, Surface } from "@/data/types";
import type { PersonRef } from "./waiting";

/** The outside places a channel is fed by, from the bot's side, read and said.
 *
 *  A WhatsApp or Telegram group, a Slack channel: each is a source of a group
 *  channel. What the bot keeps about one -- whether people outside the space
 *  are answered there, and which member answers for them -- and the ways to
 *  bring the bot into another are said from here, so the channel's panel, a
 *  bot's settings and the sheets that start a group cannot disagree. The test
 *  runner cannot load `.tsx`, so every sentence lives in this module. */

/** Where a space's bot can be in a group. Order is the menu's. */
export const GROUP_PLATFORMS = ["WHATSAPP", "TELEGRAM", "SLACK"] as const;
export type GroupPlatform = (typeof GROUP_PLATFORMS)[number];

export function isGroupPlatform(platform: string): platform is GroupPlatform {
    return (GROUP_PLATFORMS as readonly string[]).includes(platform.toUpperCase());
}

/** What a person calls the platform. */
export function platformName(platform: string): string {
    switch (platform.toUpperCase()) {
        case "WHATSAPP": return "WhatsApp";
        case "TELEGRAM": return "Telegram";
        case "SLACK": return "Slack";
        case "TEAMS": return "Teams";
        case "RESEND":
        case "EMAIL":
        case "GMAIL":
        case "OUTLOOK": return "Email";
        case "WEB": return "Web chat";
        default: return platform;
    }
}

/* ── one source's settings ─────────────────────────────────────────── */

/** What the bot keeps about one group source: who answers its people from
 *  outside the space, from what, and whether the reader may change that. */
/** A change to a group source: absent is unchanged; `owner: "me"` takes it on. */
export interface SourceChange {
    answers_outsiders?: boolean;
    owner?: "me";
}

export interface SourceSettings {
    id: string;
    channelId: string;
    platform: string;
    title: string | null;
    surfaceName: string;
    /** The group's own switch for people outside the space. */
    answersOutsiders: boolean;
    /** And a stranger asking today is answered: the switch on, a member
     *  answering for them, and the bot's own switch on. */
    welcomesOutsiders: boolean;
    /** The bot's own switch, over every group it is in. */
    botAnswersOutsiders: boolean;
    canManage: boolean;
    owner: PersonRef | null;
    inviteLink: string | null;
    pending: boolean;
    sharedExternally: boolean;
    publicReach: PublicReach | null;
}

function words(value: unknown): string | null {
    return typeof value === "string" && value.trim() ? value.trim() : null;
}

function rows(value: unknown): unknown[] {
    return Array.isArray(value) ? value : [];
}

function readPerson(raw: unknown): PersonRef | null {
    if (!raw || typeof raw !== "object") return null;
    const row = raw as Record<string, unknown>;
    const kind = row.kind === "CONTACT" || row.kind === "AGENT" ? row.kind : "MEMBER";
    return { kind, id: words(row.id), name: words(row.name) };
}

function readPublicReach(raw: unknown): PublicReach | null {
    if (!raw || typeof raw !== "object") return null;
    const row = raw as Record<string, unknown>;
    return {
        files: rows(row.files).flatMap((entry) => {
            if (!entry || typeof entry !== "object") return [];
            const file = entry as Record<string, unknown>;
            const name = words(file.name);
            return name ? [{ name, path: words(file.path) }] : [];
        }),
        tables: rows(row.tables).filter((name): name is string => typeof name === "string" && name !== ""),
        more: row.more === true,
    };
}

/** One source's settings off the wire. Null without the id everything about
 *  it is keyed by. Each switch is read the way the API defaults it: the bot's
 *  is on unless it says off, and nobody may change a source unless it says
 *  they may -- a control the server would refuse is not offered. */
export function readSourceSettings(raw: unknown): SourceSettings | null {
    if (!raw || typeof raw !== "object") return null;
    const row = raw as Record<string, unknown>;
    const id = words(row.id);
    if (!id) return null;
    const pending = row.pending === true;
    return {
        id,
        channelId: words(row.channel_id) ?? "",
        platform: (words(row.platform) ?? "").toUpperCase(),
        title: words(row.title),
        surfaceName: words(row.surface_name) ?? "",
        answersOutsiders: row.answers_outsiders === true,
        welcomesOutsiders: row.welcomes_outsiders === true,
        botAnswersOutsiders: row.bot_answers_outsiders !== false,
        canManage: row.can_manage === true,
        owner: readPerson(row.owner),
        /* A link on a group still being made would be a link to nothing. */
        inviteLink: pending ? null : words(row.invite_link),
        pending,
        sharedExternally: row.shared_externally === true,
        publicReach: readPublicReach(row.public),
    };
}

/* ── who answers people outside the space ──────────────────────────── */

/** Who answers a source's people from outside the space, as one state.
 *
 *  An internal Slack channel is its own answer: a colleague there who is not
 *  in the space is somebody to invite, and gets a private note saying so.
 *  Everywhere else three things must all hold, and where one does not, the
 *  first that fails is the reason said: the bot's own switch, then somebody
 *  in the space to answer for them, then the group's switch. A switch alone
 *  never answers anybody: on with nobody behind it answers nobody. */
export type Answering =
    | { kind: "pending" }
    | { kind: "invited" }
    | { kind: "bot-off"; platform: string }
    | { kind: "nobody" }
    /** Switched off, with somebody who would answer for them. */
    | { kind: "off"; owner: { you: boolean; name: string | null } }
    | { kind: "you" }
    | { kind: "someone"; name: string | null };

export function answering(
    source: Pick<SourceSettings, "pending" | "platform" | "sharedExternally" | "answersOutsiders" | "botAnswersOutsiders" | "owner">,
    me: string | null,
): Answering {
    if (source.pending) return { kind: "pending" };
    if (source.platform.toUpperCase() === "SLACK" && !source.sharedExternally) return { kind: "invited" };
    if (!source.botAnswersOutsiders) return { kind: "bot-off", platform: source.platform };
    const owner = source.owner;
    if (!owner) return { kind: "nobody" };
    const you = Boolean(me && owner.id === me);
    const name = you ? null : owner.name;
    if (!source.answersOutsiders) return { kind: "off", owner: { you, name } };
    return you ? { kind: "you" } : { kind: "someone", name };
}

/** The state said under a "People outside {space}" heading. */
export function sayAnswering(state: Answering, space: string): string {
    switch (state.kind) {
        case "pending": return "Not yet";
        case "invited": return "Invited to join";
        case "bot-off": return "Off for this bot";
        case "off": return "Not answered";
        case "nobody": return "Nobody answers them";
        case "you": return "You answer for them";
        case "someone": return (state.name ?? "Someone in " + space) + " answers for them";
    }
}

/** The same state as a sentence of its own, for a row with no heading. */
export function sayAnsweringFully(state: Answering, space: string): string {
    switch (state.kind) {
        case "pending": return "Being created";
        case "invited": return "People outside " + space + " are invited to join";
        case "bot-off": return "Answering people outside " + space + " is off for this " + platformName(state.platform) + " bot";
        case "off": return "People outside " + space + " are not answered";
        case "nobody": return "Nobody answers people outside " + space;
        case "you": return "You answer for people outside " + space;
        case "someone": return (state.name ?? "Someone in " + space) + " answers for people outside " + space;
    }
}

/** Whether "Take it on" is offered: only to a reader who may change the
 *  source, and only where nobody answers them or somebody else does. With the
 *  bot's switch off, taking it on would answer nobody; the bot is what to
 *  change. */
export function offersTakeOn(state: Answering, canManage: boolean): boolean {
    return canManage && (state.kind === "nobody" || state.kind === "someone");
}

/** What "Take it on" asks for. Where the group is switched off it is switched
 *  on too: the button promises that its people are answered. */
export function takeOn(source: Pick<SourceSettings, "answersOutsiders">): SourceChange {
    return source.answersOutsiders ? { owner: "me" } : { owner: "me", answers_outsiders: true };
}

/** What a reader who may not change a source is told in place of the
 *  controls: who can. */
export function onlyWho(state: Answering, space: string, bot: string): string | null {
    const theirs = (name: string | null) => name
        ? "Only " + name + " or an admin of " + space + " can change it."
        : "Only the person who answers for them or an admin of " + space + " can change it.";
    switch (state.kind) {
        case "pending":
        case "invited": return null;
        case "bot-off": return "Only someone who can change " + bot + " can turn it on.";
        case "nobody": return "Only someone who can change " + bot + " can take it on.";
        case "off": return state.owner.you ? "Only someone who can change " + bot + " can change it." : theirs(state.owner.name);
        case "you": return "Only someone who can change " + bot + " can change it.";
        case "someone": return theirs(state.name);
    }
}

/** Said to an admin about to change a source somebody else answers for: that
 *  member is told, so it is not done behind their back. */
export function toldWhenChanged(state: Answering): string | null {
    const name = state.kind === "someone" ? state.name : state.kind === "off" && !state.owner.you ? state.owner.name : undefined;
    if (name === undefined) return null;
    return (name ?? "They") + (name ? " is" : " are") + " told when you change it.";
}

/** A change the server refused: somebody else answers for it now. */
export function refusedChange(space: string): string {
    return "Only the person who answers for this group or an admin of " + space + " can change it.";
}

/** How the bot is asked something in a group, on each platform. */
export function howToAsk(platform: string, bot: string): string {
    switch (platform.toUpperCase()) {
        case "WHATSAPP": return "Answers when someone says “" + bot + "”, @mentions its number or replies to it.";
        case "TELEGRAM": return "Answers when someone @mentions it or replies to it.";
        case "SLACK": return "Answers when someone @mentions it, or replies in its thread.";
        default: return "Answers when someone mentions it or replies to it.";
    }
}

/** And whom it answers how. */
export function whomItAnswers(source: Pick<SourceSettings, "platform" | "sharedExternally" | "welcomesOutsiders">, space: string): string {
    if (source.platform.toUpperCase() === "SLACK" && !source.sharedExternally) {
        return "People in " + space + " are answered with their own access. Anyone else here gets a private note inviting them in.";
    }
    return source.welcomesOutsiders
        ? "People in " + space + " are answered with their own access. Everyone else, from what is Public."
        : "People in " + space + " are answered with their own access. Nobody else is answered here.";
}

/** How to take the bot out, where the platform lets a person do it. */
export function howToRemove(platform: string, bot: string): string | null {
    switch (platform.toUpperCase()) {
        case "TELEGRAM": return "To take " + bot + " out, remove it from the group in Telegram.";
        case "SLACK": return "To take " + bot + " out, send /remove @" + bot.replace(/^@+/, "") + " in the channel.";
        default: return null;
    }
}

/* ── bringing the bot into a group ─────────────────────────────────── */

/** Whether the space's own bot can start or join a group on a platform:
 *  connected and answering, connected and not, or not connected at all --
 *  which is offered as connecting, never as a dead option. */
export interface Starter {
    platform: GroupPlatform;
    surface: Surface | null;
    state: "ready" | "unwell" | "missing";
}

export function groupStarters(surfaces: readonly Surface[]): Starter[] {
    return GROUP_PLATFORMS.map((platform) => {
        const own = surfaces.find((surface) => surface.mine && surface.platform.toUpperCase() === platform) ?? null;
        return { platform, surface: own, state: !own ? "missing" : own.active ? "ready" : "unwell" };
    });
}

/** The first group channel fed from this platform that was not there before
 *  -- how a sheet waiting on Telegram or Slack knows the group has arrived. */
export function arrivedSince<T extends { id: string; kind: string; sources: { platform: string }[] }>(
    before: ReadonlySet<string>,
    channels: readonly T[],
    platform: string,
): T | null {
    const wanted = platform.toUpperCase();
    return channels.find((channel) => channel.kind === "GROUP" && !before.has(channel.id)
        && channel.sources.some((source) => source.platform.toUpperCase() === wanted)) ?? null;
}

/** Meta's limit on a WhatsApp group's name. */
export const WHATSAPP_TITLE_MAX = 128;

/** The words the invite is sent with, wherever it is sent. */
export function inviteText(title: string, link: string): string {
    return "Join “" + title + "” on WhatsApp: " + link;
}

/** WhatsApp's own share screen, with the invite already written. */
export function whatsappShareUrl(title: string, link: string): string {
    return "https://wa.me/?text=" + encodeURIComponent(inviteText(title, link));
}

export function inviteMailto(title: string, link: string): string {
    return "mailto:?subject=" + encodeURIComponent("Join " + title + " on WhatsApp")
        + "&body=" + encodeURIComponent(inviteText(title, link));
}

/** What to type in a Slack channel to bring the bot in. */
export function slackInvite(handle: string, fallback: string): string {
    const name = (handle.trim() || fallback.trim()).replace(/^@+/, "");
    return "/invite @" + name;
}

/** An invite link, as it reads in a narrow row: the address without its
 *  scheme. What is copied is the whole link. */
export function linkShown(link: string): string {
    return link.replace(/^https?:\/\//i, "");
}

/* ── a bot's groups, under its settings ────────────────────────────── */

/** How a group comes to exist on each platform that lists them under a bot.
 *  Telegram tells Lemma when its bot is added to a group; a WhatsApp bot
 *  opens one itself. A platform not named here lists no groups there, and so
 *  promises none -- Slack's channels are in its own settings. */
const HOW_A_GROUP_STARTS: Record<string, (bot: string) => string> = {
    TELEGRAM: (bot) => "Add " + bot + " to a Telegram group and it shows up here.",
    WHATSAPP: (bot) => "Start a WhatsApp group on " + bot + "’s number from Channels and it shows up here.",
};

export function groupsSupported(platform: string): boolean {
    return platform.toUpperCase() in HOW_A_GROUP_STARTS;
}

/** What an empty list says: how a group gets here, on this platform. */
export function noGroupsYet(platform: string, bot: string): string {
    return HOW_A_GROUP_STARTS[platform.toUpperCase()]?.(bot) ?? "No groups yet.";
}
