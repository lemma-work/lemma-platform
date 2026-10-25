import type { UserResponse } from "lemma-sdk";
import { isCompleteMobileNumber, normalizeMobileNumber, storedMobileNumber } from "@web/session/mobile-number";
import type { ProfileDraft } from "@web/session/profile-edit";

/** Proving a mobile number over WhatsApp, and the profile's own small
 *  decisions, said for a phone. Pure, so `tests/` can hold it. */

/** How long the verification is asked about between checks (the web's
 *  `VERIFICATION_POLL_MS`: nobody sends a WhatsApp message in under five
 *  seconds). */
export const VERIFY_POLL_MS = 5000;

/** Seconds left before a code runs out. */
export function secondsLeft(expiresAt: string, now = Date.now()): number {
    const at = Date.parse(expiresAt);
    return Number.isNaN(at) ? 0 : Math.max(0, Math.ceil((at - now) / 1000));
}

/** "4:05". */
export function clock(seconds: number): string {
    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

/** The server's reason, when a refusal carries one as JSON (`{"detail": …}`),
 *  else its words, else the fallback; never raw JSON. */
export function reasonOf(error: unknown, fallback: string): string {
    const said = error instanceof Error ? error.message.trim() : "";
    if (!said) return fallback;
    try {
        const parsed = JSON.parse(said) as { detail?: unknown; message?: unknown };
        const detail = parsed.detail ?? parsed.message;
        if (typeof detail === "string" && detail.trim()) return sayRefusal(detail.trim());
    } catch {
        /* Not JSON: the text is the sentence. */
    }
    if (said.startsWith("{") || said.startsWith("[") || /^(Request failed|Network request failed|Failed to fetch)/i.test(said)) return fallback;
    return sayRefusal(said);
}

/** The verification routes' refusals, said as a person would. */
function sayRefusal(said: string): string {
    if (/already in use/i.test(said)) return "Somebody else's profile already has this number.";
    if (/too many/i.test(said)) return "Too many tries for now. Wait a while, then start again.";
    return said;
}

/** Whether the number in the box is the one the server proved: a stamp
 *  belongs to the stored number, not to whatever is typed over it. */
export function isVerifiedNumber(user: Pick<UserResponse, "mobile_number" | "mobile_verified_at"> | null | undefined, typed: string): boolean {
    const number = normalizeMobileNumber(typed);
    return Boolean(user?.mobile_verified_at) && Boolean(number) && number === storedMobileNumber(user?.mobile_number ?? "");
}

/** What stops the WhatsApp button, said under it; null when it can go. */
export function verifyBlocker(typed: string): string | null {
    const number = normalizeMobileNumber(typed);
    if (!number) return "Enter a mobile number first.";
    if (!isCompleteMobileNumber(number)) return "Add the country code, starting with +, before verifying.";
    return null;
}

/** The profile's problems in the phone's words: the web's speak of a
 *  browser. */
export function phoneProblems(found: Partial<Record<keyof ProfileDraft, string>>): Partial<Record<keyof ProfileDraft, string>> {
    const out = { ...found };
    if (out.timezone) out.timezone = "That is not a time zone this phone knows.";
    return out;
}

/** Whether a WhatsApp place should say its sender will not be known: the
 *  person has no number on their profile, which is the only thing a
 *  WhatsApp message is matched on. */
export function unknownSender(user: Pick<UserResponse, "mobile_number"> | null | undefined): boolean {
    return Boolean(user) && !normalizeMobileNumber(user?.mobile_number ?? "");
}
