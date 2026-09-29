import { channelKey } from "./channel-key";
import type { Surface } from "@/data/types";

/** Where a person goes to actually say something to a connected surface.
 *
 *  `mint` for the deployment's shared Telegram bot: its bare `t.me` handle
 *  opens a chat with a bot that does not know who you are, which asks a
 *  stranger to prove themselves by email — and on an installation that cannot
 *  send mail, can never let them in. A link minted for the signed-in person
 *  opens the same bot already knowing them, and answers from this pod. */
export type SayHi = { kind: "href"; url: string } | { kind: "mint" } | null;

export function sayHi(surface: Pick<Surface, "platform" | "handle" | "email" | "system">): SayHi {
    const key = channelKey(surface.platform);
    const handle = surface.handle.trim();
    if (key === "TELEGRAM" && surface.system) return { kind: "mint" };
    if (!handle) return null;
    if (key === "TELEGRAM") return { kind: "href", url: "https://t.me/" + handle.replace(/^@/, "") };
    if (key === "WHATSAPP") return { kind: "href", url: "https://wa.me/" + handle.replace(/[^\d]/g, "") };
    if (key === "EMAIL") {
        return { kind: "href", url: "mailto:" + (surface.email || handle).trim().replace(/^(mailto:)+/i, "").replace(/\\@/g, "@") };
    }
    return null;
}
