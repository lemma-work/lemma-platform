/* The Telegram bots a member can prove their account through from their
   profile, which sits in no one space.

   The proof is the member's, not a space's. Telegram names an account by the
   same id to every bot, so an account confirmed through one bot is the
   member's in every space they are in. The bot they open the code with only
   decides which chat in Telegram it goes to. So the choice is between bots,
   not spaces: Lemma's shared bot answers for every space on it, and is one
   choice however many of the member's spaces use it. It comes first, because
   it is the chat a member most likely has already. */

import { listStamp } from "@/data/stamp";

/** One of the member's Telegram bots, in one of their spaces. */
export interface TelegramBot {
    surfaceId: string;
    podId: string;
    podName: string;
    /** `@username`, or empty while Telegram has not said. */
    handle: string;
    /** Lemma's shared bot, rather than a bot of the space's own. */
    shared: boolean;
}

/** A bot to open the code with, and the surface the code is made on. */
export interface BotChoice extends TelegramBot {
    /** The member's spaces this bot answers for. */
    spaces: string[];
}

/** One `/surfaces/me` group's Telegram surfaces, and the member's default. */
export interface MyTelegramSurfaces {
    surfaceIds: string[];
    podIds: string[];
    preferred: string | null;
}

/** The Telegram surfaces in `GET /surfaces/me`, with the spaces they are in. */
export function readMine(raw: unknown): MyTelegramSurfaces {
    const groups = (raw as { groups?: unknown } | null)?.groups;
    const group = Array.isArray(groups)
        ? groups.find((one) => (one as { platform?: unknown })?.platform === "TELEGRAM") as
            { surfaces?: unknown; default_surface_id?: unknown } | undefined
        : undefined;
    const surfaces = (Array.isArray(group?.surfaces) ? group.surfaces : [])
        .map((one) => one as { id?: unknown; pod_id?: unknown })
        .filter((one): one is { id: string; pod_id: string } => typeof one.id === "string" && typeof one.pod_id === "string");
    return {
        surfaceIds: surfaces.map((one) => one.id),
        podIds: [...new Set(surfaces.map((one) => one.pod_id))],
        preferred: typeof group?.default_surface_id === "string" ? group.default_surface_id : null,
    };
}

/** The bots to offer, Lemma's shared one first and then each space's own.
 *
 *  The shared bot's code is made on the member's default Telegram surface when
 *  that is one of them: it is the space they chose to answer them there. */
export function botChoices(bots: readonly TelegramBot[], preferred: string | null = null): BotChoice[] {
    const shared = bots.filter((bot) => bot.shared);
    const choices: BotChoice[] = [];
    if (shared.length) {
        const through = shared.find((bot) => bot.surfaceId === preferred) ?? shared[0];
        choices.push({
            ...through,
            handle: through.handle || (shared.find((bot) => bot.handle)?.handle ?? ""),
            spaces: [...new Set(shared.map((bot) => bot.podName))],
        });
    }
    const own = new Map<string, BotChoice>();
    for (const bot of bots) {
        if (bot.shared) continue;
        const key = bot.handle.toLowerCase() || bot.surfaceId;
        const known = own.get(key);
        if (!known) own.set(key, { ...bot, spaces: [bot.podName] });
        else if (!known.spaces.includes(bot.podName)) known.spaces.push(bot.podName);
    }
    return [...choices, ...[...own.values()].sort((a, b) => a.podName.localeCompare(b.podName))];
}

/** "Marketing", "Marketing and Sales", "Marketing, Sales and 2 more". */
export function spacesSaid(spaces: readonly string[]): string {
    if (spaces.length <= 2) return spaces.join(" and ");
    if (spaces.length === 3) return spaces[0] + ", " + spaces[1] + " and " + spaces[2];
    return spaces[0] + ", " + spaces[1] + " and " + (spaces.length - 2) + " more";
}

/** A Telegram account the member proved from a Lemma session. */
export interface LinkedTelegramAccount {
    id: string;
    /** The name Telegram last gave it; its @username is not kept. */
    name: string | null;
    provedAt: string | null;
}

/** `GET /surfaces/me/telegram-accounts`, read. */
export function readLinkedAccounts(raw: unknown): LinkedTelegramAccount[] {
    const items = (raw as { items?: unknown } | null)?.items;
    return (Array.isArray(items) ? items : [])
        .map((one) => one as { id?: unknown; name?: unknown; proved_at?: unknown })
        .filter((one): one is { id: string; name?: unknown; proved_at?: unknown } => typeof one.id === "string")
        .map((one) => ({
            id: one.id,
            name: typeof one.name === "string" && one.name.trim() ? one.name.trim() : null,
            provedAt: typeof one.proved_at === "string" ? one.proved_at : null,
        }));
}

/** When an account was linked, beside its name: "today", then the list's ladder. */
export function linkedWhen(iso: string | null, now = new Date()): string {
    if (!iso) return "";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "";
    return date.toDateString() === now.toDateString() ? "today" : listStamp(iso, now);
}
