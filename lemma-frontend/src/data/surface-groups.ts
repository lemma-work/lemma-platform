import type { Member, SurfaceGroup } from "./types";

/** A channel's groups, read and said.
 *
 *  Its own module, not the component's, because the test runner cannot load
 *  `.tsx` — and every sentence a group row says is decided here. */

/** How a group comes to exist on each platform that has them, as the one line
 *  an empty list says. Telegram tells Lemma when its bot is added to a group
 *  (`my_chat_member`). A WhatsApp bot cannot be added to one: it opens the
 *  group itself when a member asks it to in their own chat. A platform not
 *  named here lists no groups, and so promises none. */
const HOW_A_GROUP_STARTS: Record<string, (bot: string) => string> = {
    TELEGRAM: (bot) => "Add " + bot + " to a Telegram group and it shows up here.",
    WHATSAPP: (bot) => "Ask " + bot + " on WhatsApp to open a group and it shows up here.",
};

export function groupsSupported(platform: string): boolean {
    return platform.toUpperCase() in HOW_A_GROUP_STARTS;
}

/** What an empty list says: how a group gets here, on this platform. */
export function noGroupsYet(platform: string, bot: string): string {
    return HOW_A_GROUP_STARTS[platform.toUpperCase()]?.(bot) ?? "No groups yet.";
}

function words(value: unknown): string | null {
    return typeof value === "string" && value.trim() ? value.trim() : null;
}

/** One group off the wire. Null for a row without the id everything about it
 *  is keyed by — there would be nothing to switch. The platform's own id may
 *  be missing (a WhatsApp group still being made); nothing waits on it. */
export function readSurfaceGroup(raw: unknown): SurfaceGroup | null {
    if (!raw || typeof raw !== "object") return null;
    const row = raw as Record<string, unknown>;
    const id = words(row.id);
    if (!id) return null;
    const owner = row.owner && typeof row.owner === "object" ? (row.owner as Record<string, unknown>) : null;
    const ownerId = owner ? words(owner.user_id) : null;
    const pending = row.pending === true;
    return {
        id,
        platform: (words(row.platform) ?? "").toUpperCase(),
        title: words(row.title),
        externalId: words(row.external_channel_id),
        /* A link on a group still being made would be a link to nothing. */
        inviteLink: pending ? null : words(row.invite_link),
        pending,
        owner: ownerId ? { userId: ownerId, name: words(owner?.display_name) } : null,
        answersOutsiders: row.answers_outsiders === true,
        welcomesOutsiders: row.welcomes_outsiders === true,
        /* Each read the way the API defaults it: the bot's switch is on
           unless it says off, and nobody may change a group unless it says
           they may — a control the server would refuse is not offered. */
        botAnswersOutsiders: row.bot_answers_outsiders !== false,
        canManage: row.can_manage === true,
        updatedAt: words(row.updated_at) ?? "",
    };
}

export function readSurfaceGroups(listed: unknown): SurfaceGroup[] {
    const items = listed && typeof listed === "object" ? (listed as { items?: unknown }).items : null;
    return (Array.isArray(items) ? items : [])
        .map(readSurfaceGroup)
        .filter((group): group is SurfaceGroup => group !== null);
}

/** What the platform calls it, or "Group 3f9a1c" when it gave no title. */
export function groupTitle(group: SurfaceGroup): string {
    return group.title ?? "Group " + group.id.replace(/[^0-9a-z]/gi, "").slice(0, 6).toLowerCase();
}

/** The row's one line: a group still being made says so; any other says who
 *  answers there for people outside the space — or, where nobody does, the
 *  first reason why: the bot's own switch, then nobody to answer for them,
 *  then the group's switch.
 *
 *  A person is named only while `welcomesOutsiders` holds: an owner with a
 *  switch off answers nobody, and a line naming a person who is not
 *  answering would be the one false thing here. */
export function groupLine(group: SurfaceGroup, space: string, me: string | null, members: Member[] = []): string {
    if (group.pending) return "Being created…";
    if (!group.botAnswersOutsiders) return "Answering people outside " + space + " is off for this bot";
    const owner = group.owner;
    if (!owner) return "Nobody answers for people outside " + space + " yet";
    if (!group.answersOutsiders || !group.welcomesOutsiders) return "People outside " + space + " are not answered here";
    if (me && owner.userId === me) return "You answer for people outside " + space;
    const name = owner.name ?? members.find((member) => member.userId === owner.userId)?.name ?? "Someone in " + space;
    return name + " answers for people outside " + space;
}

/** An invite link, as it reads in a narrow row: the address without its
 *  scheme. What is copied is the whole link. */
export function linkShown(link: string): string {
    return link.replace(/^https?:\/\//i, "");
}

/** Whether "Take this over" is offered: only where the reader may change the
 *  group and its bot answers anybody outside the space at all — then where
 *  somebody else answers for it, or it is switched on with nobody answering.
 *  Where it is off and nobody answers, switching it on already makes you the
 *  one who does. */
export function offersTakeOver(group: SurfaceGroup, me: string | null): boolean {
    if (!group.canManage || !group.botAnswersOutsiders) return false;
    if (group.owner) return group.owner.userId !== me;
    return group.answersOutsiders;
}

/** What a reader who may not change a group is told instead of a control:
 *  who can. The member answering for it, or an admin of the space; where
 *  nobody does — or it is the reader, who may no longer change the bot —
 *  whoever may change the bot. */
export function whoMayChange(group: Pick<SurfaceGroup, "owner">, space: string, bot: string, me: string | null, members: Member[] = []): string {
    const owner = group.owner;
    if (!owner || (me && owner.userId === me)) return "Only someone who can change " + bot + " can change it.";
    const name = owner.name ?? members.find((member) => member.userId === owner.userId)?.name ?? null;
    return name
        ? "Only " + name + " or an admin of " + space + " can change it."
        : "Only the person who answers for it or an admin of " + space + " can change it.";
}

/** The list with one group replaced by what the server saved. */
export function withGroup(list: SurfaceGroup[], saved: SurfaceGroup): SurfaceGroup[] {
    return list.map((group) => (group.id === saved.id ? saved : group));
}
