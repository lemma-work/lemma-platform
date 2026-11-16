/* Proving a Telegram account is the member's, without the drawing: what the
   server's answers say, which step the proof is at, and how the account is
   named. Pure, so the phone runs it where it lives as the web does
   (`identity-link.tsx` draws it here). */

export interface TelegramAccount {
    name: string | null;
    username: string | null;
}

export type IdentityLinkStatus =
    | { state: "WAITING" | "OTHER_ACCOUNT" | "GONE" }
    | { state: "OPENED"; account: TelegramAccount };

/** How often a code not yet opened is asked about. */
export const LINK_POLL_MS = 2_000;

/** Where the proof stands: the code to open, the account to confirm, done,
 *  or ended without a link. */
export type LinkStep = "open" | "confirm" | "linked" | "other-account" | "gone";

export function linkStep(status: IdentityLinkStatus | null | undefined, linked: boolean): LinkStep {
    if (linked) return "linked";
    if (status?.state === "OPENED") return "confirm";
    if (status?.state === "OTHER_ACCOUNT") return "other-account";
    if (status?.state === "GONE") return "gone";
    return "open";
}

/** Whether nothing is left to do but close it. */
export function linkEnded(step: LinkStep): boolean {
    return step === "linked" || step === "other-account" || step === "gone";
}

/** An account as the server names it. */
export function readTelegramAccount(raw: { name?: string | null; username?: string | null }): TelegramAccount {
    return { name: raw.name ?? null, username: raw.username ?? null };
}

/** `GET /pods/{pod}/telegram-identity-links/{code}`, read. An opened code
 *  with no account is as good as gone. */
export function readIdentityLinkStatus(found: { state: string; account?: { name?: string | null; username?: string | null } | null }): IdentityLinkStatus {
    if (found.state === "OPENED" && found.account) return { state: "OPENED", account: readTelegramAccount(found.account) };
    if (found.state === "WAITING" || found.state === "OTHER_ACCOUNT") return { state: found.state };
    return { state: "GONE" };
}

/** "Ada L. (@ada_l)", whichever of the two Telegram gave. */
export function accountSaid(account: TelegramAccount): string {
    const name = account.name?.trim();
    const handle = account.username ? "@" + account.username : null;
    if (name && handle) return name + " (" + handle + ")";
    return name || handle || "this account";
}
