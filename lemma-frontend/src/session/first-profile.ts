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
 *  Timezone is confirmed in the dialog when it opens, or written from the
 *  browser in the background when OAuth already supplied a name — it is never
 *  itself a reason to interrupt.
 */

/** Only new accounts. The step belongs to the first days of an account, not
 *  to everybody the day it ships: an older account missing a phone has been
 *  using the app without one and can add it in Settings. A week is enough to
 *  cover somebody who signed up and came back a few days later. */
export const NEW_ACCOUNT_MS = 7 * 24 * 60 * 60 * 1000;
/** Delays between the initial timezone write and its bounded retries. */
export const TIMEZONE_RETRY_DELAYS = [1_000, 2_000, 4_000] as const;

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

/** Whether to write the browser's zone onto a new account that still has none.
 *
 *  The dialog owns the field while it is open. Outside it — OAuth already
 *  named them, or they skipped — a never-set zone leaves every schedule and
 *  run stamp without a conversion target. An explicit empty timezone from
 *  Settings is left alone.
 */
export function needsTimezoneSync(
    user: Pick<UserResponse, "timezone" | "created_at">,
    { dialogOpen, now = Date.now() }: { dialogOpen: boolean; now?: number },
): boolean {
    if (dialogOpen) return false;
    if (user.timezone !== null && user.timezone !== undefined) return false;
    const created = Date.parse(user.created_at);
    if (Number.isNaN(created) || now - created > NEW_ACCOUNT_MS) return false;
    return true;
}

/** Dismissal is disabled while the profile save is in flight. */
export function canDismissFirstProfile(savePending: boolean): boolean {
    return !savePending;
}

/** Save a detected timezone with bounded retries until the owning effect ends. */
export function syncDetectedTimezone<T>(
    save: () => Promise<T>,
    onSuccess: (next: T) => void,
    onFailure: () => void,
): () => void {
    let active = true;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    let retryCount = 0;

    const sync = () => {
        if (!active) return;
        void save().then(
            (next) => {
                if (active) onSuccess(next);
            },
            () => {
                if (!active) return;
                onFailure();
                const delay = TIMEZONE_RETRY_DELAYS[retryCount];
                if (delay === undefined) return;
                retryCount += 1;
                retryTimer = setTimeout(sync, delay);
            },
        );
    };
    sync();
    return () => {
        active = false;
        if (retryTimer !== undefined) clearTimeout(retryTimer);
    };
}

/** Continued or skipped, per person and per browser. Kept here rather than on
 *  the account because the account has nowhere to hold it yet; the cost is
 *  that somebody who skipped is asked once more on a second device, inside the
 *  same first week. */
export function settledKey(userId: string): string {
    return key("first-profile:" + userId);
}
