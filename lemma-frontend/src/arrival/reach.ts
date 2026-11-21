/** Where a new Lemma can be reached from, on the way in.
 *
 *  Every place is shown, so the person sees what a Lemma can do even where
 *  this server cannot do it yet. Each is one of three things:
 *
 *  - `on`: it can be written to now -- the email address a Lemma's AI starts
 *    with, or a place already connected;
 *  - `off`: one tap turns it on here -- a shared WhatsApp number or Telegram
 *    bot the deployment runs (`effort: "instant"`), or Lemma's own Slack app;
 *  - `missing`: not here, said plainly. `why` is "later" where the person
 *    could connect their own bot or app from the Lemma afterwards, and
 *    "server" where this server has nothing set up for it. */

export type PlaceKey = "EMAIL" | "WHATSAPP" | "TELEGRAM" | "SLACK";

export interface Place {
    key: PlaceKey;
    state: "on" | "off" | "missing";
    /** Where you write to it: an address, a number, a bot's name. */
    handle: string | null;
    why?: "later" | "server";
}

/** RESEND, GMAIL and OUTLOOK are all email to the person writing. */
export function placeKeyOf(platform: string): PlaceKey | null {
    const key = platform.toUpperCase();
    if (["EMAIL", "RESEND", "GMAIL", "OUTLOOK"].includes(key)) return "EMAIL";
    return key === "WHATSAPP" || key === "TELEGRAM" || key === "SLACK" ? key : null;
}

export function placesFrom({ connectable, surfaces, slack, team }: {
    connectable: readonly { platform: string; effort: string }[];
    surfaces: readonly { platform: string; handle: string; email?: string; active?: boolean }[];
    /** Lemma's own Slack app can be installed here. */
    slack: boolean;
    /** A team's Lemma leads with Slack, where a team already talks. */
    team: boolean;
}): Place[] {
    const live = (key: PlaceKey) => surfaces.find((one) => placeKeyOf(one.platform) === key && one.active !== false) ?? null;
    const effort = (key: PlaceKey) => connectable.find((one) => placeKeyOf(one.platform) === key)?.effort ?? "unavailable";
    const order: PlaceKey[] = team ? ["SLACK", "EMAIL", "WHATSAPP", "TELEGRAM"] : ["WHATSAPP", "EMAIL", "TELEGRAM", "SLACK"];
    const places = order.map((key): Place => {
        const surface = live(key);
        if (surface) {
            const handle = (key === "EMAIL" ? surface.email || surface.handle : surface.handle).trim();
            return { key, state: "on", handle: handle || null };
        }
        if (key === "EMAIL") return { key, state: "missing", handle: null, why: "server" };
        if (key === "SLACK" ? slack : effort(key) === "instant") return { key, state: "off", handle: null };
        const later = effort(key) === "guided" || effort(key) === "account";
        return { key, state: "missing", handle: null, why: later ? "later" : "server" };
    });
    /* What can be used here first; what cannot, after, in the same order. */
    return [...places.filter((one) => one.state !== "missing"), ...places.filter((one) => one.state === "missing")];
}

/** The link that opens a chat with it, a hello already typed where the app
 *  allows one. Email has no link worth giving; its address is copied. */
export function helloLink(key: PlaceKey, handle: string | null, lemmaName: string): string | null {
    const said = (handle ?? "").trim();
    if (!said) return null;
    if (key === "WHATSAPP") {
        const digits = said.replace(/[^\d]/g, "");
        return digits ? `https://wa.me/${digits}?text=${encodeURIComponent(`Hi ${lemmaName}`)}` : null;
    }
    if (key === "TELEGRAM") return `https://t.me/${said.replace(/^@/, "")}`;
    return null;
}

/** The first place they wrote to it from since `since` (ms), if any:
 *  conversations record the platform a surface heard them on. */
export function heardOn(
    conversations: readonly { origin?: { kind: string; platform?: string }; activityAt?: string | null }[],
    on: readonly PlaceKey[],
    since: number,
): PlaceKey | null {
    for (const one of conversations) {
        if (one.origin?.kind !== "channel" || !one.origin.platform) continue;
        const key = placeKeyOf(one.origin.platform);
        if (!key || !on.includes(key)) continue;
        const at = one.activityAt ? Date.parse(one.activityAt) : NaN;
        if (Number.isFinite(at) && at >= since) return key;
    }
    return null;
}
