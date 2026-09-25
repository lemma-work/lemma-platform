import type { UserResponse } from "lemma-sdk";
import { key } from "./storage";

/** The one step between signing up and the app: your name, and your phone.
 *
 *  Both are things teammates already depend on and nobody asked for. A
 *  passwordless sign-up arrives with no name at all, so the account is called
 *  by the local part of an email address everywhere it appears. And WhatsApp
 *  knows a sender only by their number: a person with none on their profile
 *  messages their own teammate and gets a sign-up link back.
 *
 *  So this asks for exactly what is missing and nothing that is not, which is
 *  also why it is decided here from state rather than remembered as a step
 *  somebody reached. An account that already has both is never shown it.
 */

/** Only new accounts. The step belongs to the first days of an account, not
 *  to everybody the day it ships: an older account missing a phone has been
 *  using the app without one and can add it in Settings. A week is enough to
 *  cover somebody who signed up and came back a few days later. */
export const NEW_ACCOUNT_MS = 7 * 24 * 60 * 60 * 1000;

export interface FirstProfileNeeds {
    /** No first name on the account. The step still offers the name when this
     *  is false — confirming one a provider supplied costs a glance — but a
     *  name on its own is never a reason to interrupt. */
    name: boolean;
    /** No mobile number, and this deployment can verify one over WhatsApp.
     *  Without that the step would have to ask for a number to be typed, and a
     *  typed number is taken on trust by the sender match, so it never does. */
    phone: boolean;
}

export function firstProfileNeeds(
    user: Pick<UserResponse, "first_name" | "mobile_number">,
    { whatsApp }: { whatsApp: boolean },
): FirstProfileNeeds {
    return {
        name: !(user.first_name ?? "").trim(),
        phone: whatsApp && !(user.mobile_number ?? "").trim(),
    };
}

/** Whether to show the step to this person, now. */
export function asksFirstProfile(
    user: Pick<UserResponse, "first_name" | "mobile_number" | "created_at">,
    { whatsApp, settled, now = Date.now() }: { whatsApp: boolean; settled: boolean; now?: number },
): boolean {
    if (settled) return false;
    const created = Date.parse(user.created_at);
    if (Number.isNaN(created) || now - created > NEW_ACCOUNT_MS) return false;
    const needs = firstProfileNeeds(user, { whatsApp });
    return needs.name || needs.phone;
}

/** Continued or skipped, per person and per browser. Kept here rather than on
 *  the account because the account has nowhere to hold it yet; the cost is
 *  that somebody who skipped is asked once more on a second device, inside the
 *  same first week. */
export function settledKey(userId: string): string {
    return key("first-profile:" + userId);
}
