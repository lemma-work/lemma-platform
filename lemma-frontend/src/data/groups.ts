import type { Group, GroupDetail, GroupLine, GroupPerson, GroupQuestion, Member, Surface } from "./types";
import { readSurfaceGroup } from "./surface-groups";

/** A space's groups, read and said.
 *
 *  The Groups page, one group's page and the sheets that start or add one all
 *  say their sentences from here, so the list and the page cannot disagree
 *  about who answers a group — and the test runner, which cannot load `.tsx`,
 *  can hold every one of them to what the API actually said. */

/** Where a space's bot can be in a group. Order is the New group menu's. */
export const GROUP_PLATFORMS = ["WHATSAPP", "TELEGRAM", "SLACK"] as const;
export type GroupPlatform = (typeof GROUP_PLATFORMS)[number];

export function isGroupPlatform(platform: string): platform is GroupPlatform {
    return (GROUP_PLATFORMS as readonly string[]).includes(platform.toUpperCase());
}

export function platformName(platform: string): string {
    const key = platform.toUpperCase();
    return key === "WHATSAPP" ? "WhatsApp" : key === "TELEGRAM" ? "Telegram" : key === "SLACK" ? "Slack" : platform;
}

/** "Telegram group", "Slack channel": what the platform calls the place. */
export function placeName(platform: string): string {
    return platformName(platform) + (platform.toUpperCase() === "SLACK" ? " channel" : " group");
}

/** Whether the space keeps what is said in the group. Telegram and WhatsApp
 *  keep no history a bot may read, so the space logs it; a Slack channel's
 *  history is Slack's, and nothing of it is copied. */
export function keepsTimeline(platform: string): boolean {
    return platform.toUpperCase() !== "SLACK";
}

/* ── off the wire ──────────────────────────────────────────────────── */

function words(value: unknown): string | null {
    return typeof value === "string" && value.trim() ? value.trim() : null;
}

function count(value: unknown): number | null {
    return typeof value === "number" && Number.isFinite(value) && value >= 0 ? Math.floor(value) : null;
}

function rows(value: unknown): unknown[] {
    return Array.isArray(value) ? value : [];
}

function present<T>(value: T | null): value is T {
    return value !== null;
}

/** One group off the space-wide list. Null without the id everything about
 *  it is keyed by. */
export function readGroup(raw: unknown): Group | null {
    const base = readSurfaceGroup(raw);
    if (!base) return null;
    const row = raw as Record<string, unknown>;
    return {
        ...base,
        surfaceName: words(row.surface_name) ?? "",
        sharedExternally: row.shared_externally === true,
        peopleInSpace: count(row.people_in_pod),
        peopleOutside: count(row.people_outside),
        lastMessageAt: words(row.last_message_at),
        waitingForYou: count(row.waiting_for_you) ?? 0,
    };
}

export function readGroups(listed: unknown): Group[] {
    const items = listed && typeof listed === "object" ? (listed as { items?: unknown }).items : null;
    return rows(items).map(readGroup).filter(present);
}

function readPerson(raw: unknown): GroupPerson | null {
    if (!raw || typeof raw !== "object") return null;
    const row = raw as Record<string, unknown>;
    const name = words(row.name) ?? words(row.external_id);
    if (!name) return null;
    return { name, externalId: words(row.external_id), userId: words(row.user_id), inSpace: row.in_pod === true };
}

function readQuestion(raw: unknown): GroupQuestion | null {
    if (!raw || typeof raw !== "object") return null;
    const row = raw as Record<string, unknown>;
    const notificationId = words(row.notification_id);
    const question = words(row.question);
    /* Without its notification there is nothing to answer it through. */
    if (!notificationId || !question) return null;
    return { notificationId, question, askedAt: words(row.asked_at) ?? "" };
}

export function readGroupDetail(raw: unknown): GroupDetail | null {
    const group = readGroup(raw);
    if (!group) return null;
    const row = raw as Record<string, unknown>;
    return {
        ...group,
        people: rows(row.people).map(readPerson).filter(present),
        waiting: rows(row.waiting).map(readQuestion).filter(present),
    };
}

function readLine(raw: unknown): GroupLine | null {
    if (!raw || typeof raw !== "object") return null;
    const row = raw as Record<string, unknown>;
    const at = words(row.at);
    if (typeof row.text !== "string" || !at) return null;
    const fromBot = row.from_bot === true;
    return {
        authorName: words(row.author_name),
        authorExternalId: words(row.author_external_id),
        inSpace: fromBot || row.in_pod === true,
        fromBot,
        text: row.text,
        at,
        answeredName: words(row.answered_name),
        answeredFromPublic: row.answered_from_public === true,
    };
}

/** What was said, oldest first — the API's order, kept stable where two
 *  lines share a second. */
export function readTimeline(listed: unknown): GroupLine[] {
    const items = listed && typeof listed === "object" ? (listed as { items?: unknown }).items : null;
    return rows(items)
        .map(readLine)
        .filter(present)
        .map((line, index) => ({ line, index, time: Date.parse(line.at) }))
        .sort((a, b) => (a.time - b.time) || (a.index - b.index))
        .map(({ line }) => line);
}

/* ── who answers people outside the space ──────────────────────────── */

/** Who answers a group's people from outside the space, as one state.
 *
 *  An internal Slack channel is its own answer: a colleague there who is not
 *  in the space is somebody to invite, and gets a private note saying so —
 *  nobody is answered from what is Public in front of the channel. Everywhere
 *  else the switch and the owner decide it, and the switch alone never does:
 *  on with nobody behind it answers nobody. */
export type Answering =
    | { kind: "pending" }
    | { kind: "invited" }
    | { kind: "off" }
    | { kind: "nobody" }
    | { kind: "you" }
    | { kind: "someone"; name: string | null };

export function answering(
    group: Pick<Group, "pending" | "platform" | "sharedExternally" | "answersOutsiders" | "owner">,
    me: string | null,
    members: readonly Member[] = [],
): Answering {
    if (group.pending) return { kind: "pending" };
    if (group.platform.toUpperCase() === "SLACK" && !group.sharedExternally) return { kind: "invited" };
    if (!group.answersOutsiders) return { kind: "off" };
    const owner = group.owner;
    if (!owner) return { kind: "nobody" };
    if (me && owner.userId === me) return { kind: "you" };
    return { kind: "someone", name: owner.name ?? members.find((member) => member.userId === owner.userId)?.name ?? null };
}

/** The state said under a "People outside {space}" heading, where the
 *  heading already names who "them" is. */
export function sayAnswering(state: Answering, space: string): string {
    switch (state.kind) {
        case "pending": return "Not yet";
        case "invited": return "Invited to join";
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
        case "off": return "People outside " + space + " are not answered";
        case "nobody": return "Nobody answers people outside " + space;
        case "you": return "You answer for people outside " + space;
        case "someone": return (state.name ?? "Someone in " + space) + " answers for people outside " + space;
    }
}

/** Whether "Take it on" is offered: nobody answers them, or somebody else
 *  does. Switched off, turning it on is what makes you the one who answers. */
export function offersTakeOn(state: Answering): boolean {
    return state.kind === "nobody" || state.kind === "someone";
}

/* ── what a row says ───────────────────────────────────────────────── */

function people(count: number): string {
    return count === 1 ? "1 person" : count + " people";
}

/** Who has spoken there, in and outside the space. Counted from the space's
 *  log, so it is people seen speaking — and nothing for Slack, which keeps
 *  its own. */
function whoIsThere(group: Group, space: string, now = new Date()): string | null {
    if (group.pending) return confirmationOverdue(group, now) ? "waiting for WhatsApp to confirm it" : "being created, a few seconds";
    if (group.platform.toUpperCase() === "SLACK") {
        return group.sharedExternally ? "shared with another company" : "inside your company";
    }
    if (group.peopleInSpace === null && group.peopleOutside === null) return null;
    const inside = group.peopleInSpace ?? 0;
    const outside = group.peopleOutside ?? 0;
    if (inside + outside === 0) return "nobody has spoken yet";
    if (outside === 0) return people(inside) + " in " + space;
    if (inside === 0) return people(outside) + " outside " + space;
    return inside + " in " + space + ", " + outside + " outside";
}

/** WhatsApp confirms a new group in seconds. Past a couple of minutes it is
 *  not coming soon, and "a few seconds" would keep promising that it is. */
const CONFIRMATION_OVERDUE_MS = 2 * 60_000;

function confirmationOverdue(group: Group, now: Date): boolean {
    const asked = Date.parse(group.updatedAt);
    return Number.isFinite(asked) && now.getTime() - asked > CONFIRMATION_OVERDUE_MS;
}

/** A row's one line: the platform, the agent answering there when it is not
 *  the space's own, and who is in it. */
export function groupSubline(group: Group, space: string, agent: string | null = null, now = new Date()): string {
    return [platformName(group.platform), agent, whoIsThere(group, space, now)].filter(Boolean).join(" · ");
}

/** The page's line under the group's name. */
export function groupHeadline(group: GroupDetail, agent: string | null = null): string {
    const parts: string[] = [placeName(group.platform)];
    if (agent) parts.push(agent + " answers there");
    if (group.pending) parts.push("being created");
    else if (group.platform.toUpperCase() === "SLACK") parts.push(group.sharedExternally ? "shared with another company" : "inside your company");
    else if (group.people.length > 0) parts.push(people(group.people.length) + (group.people.length === 1 ? " has" : " have") + " spoken here");
    else parts.push("nobody has spoken yet");
    return parts.join(" · ");
}

/** When something happened, short enough for a column: 12 min, 2 h,
 *  Yesterday, 3 days, then the date. */
export function sinceShort(iso: string | null | undefined, now = new Date()): string {
    if (!iso) return "";
    const at = new Date(iso);
    if (Number.isNaN(at.getTime())) return "";
    const minutes = Math.floor((now.getTime() - at.getTime()) / 60_000);
    if (minutes < 1) return "Now";
    if (minutes < 60) return minutes + " min";
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return hours + " h";
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    const day = new Date(at.getFullYear(), at.getMonth(), at.getDate()).getTime();
    const days = Math.round((today - day) / 86_400_000);
    if (days <= 1) return "Yesterday";
    if (days < 7) return days + " days";
    return at.toLocaleDateString([], at.getFullYear() === now.getFullYear()
        ? { day: "numeric", month: "short" }
        : { day: "numeric", month: "short", year: "numeric" });
}

/** When a row last moved: its last message, or — for a group still being
 *  made, which has none — when it was asked for. */
export function lastActive(group: Group): string | null {
    return group.lastMessageAt ?? (group.pending ? group.updatedAt : null);
}

/** Most recently active first, which is how a list of chats reads. */
export function byActivity(groups: readonly Group[]): Group[] {
    const when = (group: Group) => Date.parse(lastActive(group) ?? group.updatedAt) || 0;
    return [...groups].sort((a, b) => when(b) - when(a));
}

/* ── waiting on you ────────────────────────────────────────────────── */

export function waitingTotal(groups: readonly Pick<Group, "waitingForYou">[]): number {
    return groups.reduce((sum, group) => sum + group.waitingForYou, 0);
}

/** The groups with something waiting on you, most first. */
export function waitingGroups(groups: readonly Group[]): Group[] {
    return byActivity(groups.filter((group) => group.waitingForYou > 0))
        .sort((a, b) => b.waitingForYou - a.waitingForYou);
}

/** "1 question waiting for you", for the sidebar badge's accessible name. */
export function sayWaiting(count: number): string {
    return count === 1 ? "1 question waiting for you" : count + " questions waiting for you";
}

/* ── starting or adding one ────────────────────────────────────────── */

/** Whether the space's own bot can start or join a group on a platform:
 *  connected and answering, connected and not (paused, unfinished), or not
 *  connected at all — which is offered as connecting, never as a dead
 *  option. The space's own bot only: a group started from here is answered
 *  as the space. */
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

/** The first group on this channel that was not there before — how a sheet
 *  waiting on Telegram or Slack knows the group has arrived. */
export function arrivedSince(before: ReadonlySet<string>, groups: readonly Group[], surfaceName: string): Group | null {
    return groups.find((group) => group.surfaceName === surfaceName && !before.has(group.id)) ?? null;
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

/** Slack's own link into a channel, where the channel's id is known. */
export function slackChannelUrl(channelId: string | null): string | null {
    return channelId ? "https://slack.com/app_redirect?channel=" + encodeURIComponent(channelId) : null;
}

/* ── one group's page ──────────────────────────────────────────────── */

/** Where a person stands: in the space, a Lemma account that is not, or a
 *  chat account Lemma cannot tell apart from a stranger. The last is not
 *  offered anything — linking somebody else's account to a person would hand
 *  them that person's access. */
export type Standing = "in" | "outside" | "unknown";

export function standing(person: GroupPerson): Standing {
    if (person.inSpace) return "in";
    return person.userId ? "outside" : "unknown";
}

export function sayStanding(state: Standing, space: string): string {
    return state === "in" ? "In " + space : state === "outside" ? "Not in " + space : "Not recognised";
}

/** Two letters for a person's face; a question mark for somebody Lemma
 *  cannot place, whose name is often only a handle or a number. */
export function personInitials(name: string, state: Standing = "outside"): string {
    if (state === "unknown") return "?";
    const parts = name.replace(/^[@+]+/, "").split(/[\s._-]+/).filter(Boolean);
    const letters = parts.slice(0, 2).map((word) => word[0] ?? "").join("");
    return (letters || name.slice(0, 1) || "?").toUpperCase();
}

/** Who wrote a line, as the reader sees it: "You" for the reader's own. */
export function lineAuthor(line: GroupLine, people: readonly GroupPerson[], me: string | null, bot: string): { name: string; you: boolean } {
    if (line.fromBot) return { name: bot, you: false };
    const person = line.authorExternalId ? people.find((one) => one.externalId === line.authorExternalId) : undefined;
    if (me && person?.userId === me) return { name: "You", you: true };
    return { name: line.authorName ?? person?.name ?? line.authorExternalId ?? "Someone", you: false };
}

/** The small print under a bot's line: whom it answered, and on whose
 *  access. */
export function answeredNote(line: GroupLine): string | null {
    if (!line.fromBot) return null;
    if (line.answeredFromPublic) {
        return line.answeredName ? "Answered " + line.answeredName + " from what is Public" : "Answered from what is Public";
    }
    return line.answeredName ? "Answered " + line.answeredName + ", with their access" : null;
}

/** The timeline, cut at each day, the days named the way a chat names
 *  them. */
export function timelineDays(lines: readonly GroupLine[], now = new Date()): { key: string; label: string; lines: GroupLine[] }[] {
    const days: { key: string; label: string; lines: GroupLine[] }[] = [];
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    for (const line of lines) {
        const at = new Date(line.at);
        if (Number.isNaN(at.getTime())) continue;
        const day = new Date(at.getFullYear(), at.getMonth(), at.getDate());
        const key = day.toDateString();
        const last = days.at(-1);
        if (last?.key === key) { last.lines.push(line); continue; }
        const back = Math.round((today - day.getTime()) / 86_400_000);
        const label = back === 0 ? "Today" : back === 1 ? "Yesterday"
            : at.toLocaleDateString([], at.getFullYear() === now.getFullYear()
                ? { weekday: "long", day: "numeric", month: "long" }
                : { day: "numeric", month: "long", year: "numeric" });
        days.push({ key, label, lines: [line] });
    }
    return days;
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
export function whomItAnswers(group: Pick<Group, "platform" | "sharedExternally">, space: string): string {
    if (group.platform.toUpperCase() === "SLACK" && !group.sharedExternally) {
        return "People in " + space + " are answered with their own access. Anyone else here gets a private note inviting them in.";
    }
    return "People in " + space + " are answered with their own access. Everyone else, from what is Public.";
}

/** How to take the bot out, where the platform lets a person do it. A
 *  WhatsApp group is one the bot made, so there is nothing to say there. */
export function howToRemove(platform: string, bot: string): string | null {
    switch (platform.toUpperCase()) {
        case "TELEGRAM": return "To take " + bot + " out, remove it from the group in Telegram.";
        case "SLACK": return "To take " + bot + " out, send /remove @" + bot.replace(/^@+/, "") + " in the channel.";
        default: return null;
    }
}
