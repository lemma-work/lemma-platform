import type { UserProfileRequest, UserResponse } from "lemma-sdk";

/** Editing the person behind the work, here rather than somewhere else.
 *
 *  The fields are the ones the profile endpoint accepts, and nothing else on
 *  the user is editable: email, verification and active state are answers the
 *  server gives, not things a form may set.
 */

/** The editable shape, as strings, because a form holds strings. */
export interface ProfileDraft {
    first_name: string;
    last_name: string;
    timezone: string;
    country: string;
    mobile_number: string;
    telegram_username: string;
    date_of_birth: string;
}

export const PROFILE_FIELDS: (keyof ProfileDraft)[] = [
    "first_name", "last_name", "timezone", "country",
    "mobile_number", "telegram_username", "date_of_birth",
];

function text(value: string | null | undefined): string {
    return (value ?? "").trim();
}

/** What the server currently holds, as something a form can hold too. */
export function draftOf(user: UserResponse | null | undefined): ProfileDraft {
    return {
        first_name: text(user?.first_name),
        last_name: text(user?.last_name),
        timezone: text(user?.timezone),
        country: text(user?.country),
        mobile_number: text(user?.mobile_number),
        /* Stored bare; the @ is how people write it and how it is shown. */
        telegram_username: text(user?.telegram_username).replace(/^@+/, ""),
        date_of_birth: text(user?.date_of_birth).slice(0, 10),
    };
}

/** Only what actually changed, and `null` where it was deliberately emptied.
 *
 *  The distinction is the point. Sending every field back means a form that
 *  loaded while one value was still in flight can write a stale copy over the
 *  newer one; sending nothing for an emptied field leaves it set forever, so
 *  clearing a phone number would silently not work. Unchanged is omitted,
 *  cleared is an explicit null.
 */
export function changes(before: ProfileDraft, after: ProfileDraft): UserProfileRequest {
    const patch: Record<string, string | null> = {};
    for (const field of PROFILE_FIELDS) {
        const was = before[field];
        const now = field === "telegram_username" ? after[field].replace(/^@+/, "").trim() : after[field].trim();
        if (was === now) continue;
        patch[field] = now === "" ? null : now;
    }
    return patch as UserProfileRequest;
}

export function hasChanges(before: ProfileDraft, after: ProfileDraft): boolean {
    return Object.keys(changes(before, after)).length > 0;
}

/** What cannot be sent, said before the round trip rather than after.
 *
 *  Deliberately thin: the server owns the rules, and a form inventing its own
 *  ends up refusing things the API would have accepted. Only the two that are
 *  unambiguously wrong are caught here.
 */
export function problems(draft: ProfileDraft, today = new Date()): Partial<Record<keyof ProfileDraft, string>> {
    const found: Partial<Record<keyof ProfileDraft, string>> = {};
    const born = draft.date_of_birth.trim();
    if (born) {
        if (!/^\d{4}-\d{2}-\d{2}$/.test(born)) {
            found.date_of_birth = "Use a date like 1990-04-23.";
        } else {
            const at = Date.parse(born + "T12:00:00Z");
            if (Number.isNaN(at)) found.date_of_birth = "That is not a real date.";
            else if (at > today.getTime()) found.date_of_birth = "That date has not happened yet.";
        }
    }
    const zone = draft.timezone.trim();
    if (zone && !isTimezone(zone)) found.timezone = "That is not a timezone this browser knows.";
    return found;
}

/** Whether a string names a zone. Asked of the runtime rather than a list,
 *  because the list changes and the runtime is what will format the dates. */
export function isTimezone(value: string): boolean {
    try {
        new Intl.DateTimeFormat("en-US", { timeZone: value });
        return true;
    } catch {
        return false;
    }
}

/** The zone this browser is in — the sensible default for somebody who has
 *  never set one, which is everybody until they open this form. */
export function localTimezone(): string {
    try {
        return Intl.DateTimeFormat().resolvedOptions().timeZone || "";
    } catch {
        return "";
    }
}

/** Every zone worth offering, newest runtimes first and a small fallback for
 *  the ones that cannot enumerate them.
 *
 *  Both branches end the same way, and they have to: **the list must contain
 *  the zone the reader is actually in**, or the form opens unable to show them
 *  their own setting. The short list has always said so; the enumerated one
 *  handed back whatever the runtime gave it and assumed the two agreed.
 *
 *  They do not. `supportedValuesOf` lists canonical zones, and a browser
 *  resolves to whatever its host is set to, which is frequently an alias that
 *  is not on it. `UTC` is the one that matters — it is what every CI runner and
 *  most servers report, and it is *not* in the list of 418. Nor is `Etc/UTC`,
 *  nor `US/Pacific`. Which spelling is canonical is an ICU build's business and
 *  moves underneath us: this machine has `Asia/Calcutta` and not
 *  `Asia/Kolkata`, and the fallback list below hardcodes the other one.
 *
 *  So the local zone goes on the front wherever it is missing, rather than this
 *  trying to know which name any particular runtime prefers.
 */
export function timezones(): string[] {
    const here = localTimezone();
    const withHere = (all: string[]) => (here && !all.includes(here) ? [here, ...all] : all);
    try {
        const supported = (Intl as unknown as { supportedValuesOf?: (key: string) => string[] }).supportedValuesOf;
        if (typeof supported === "function") return withHere(supported("timeZone"));
    } catch {
        /* fall through to the short list */
    }
    return withHere(["UTC", "Europe/London", "Europe/Berlin", "Asia/Kolkata", "Asia/Singapore",
        "America/New_York", "America/Los_Angeles", "Australia/Sydney"]);
}

/** What to call somebody, given how little the server may know. */
export function displayName(user: UserResponse | null | undefined, fallback = "Your profile"): string {
    const full = [text(user?.first_name), text(user?.last_name)].filter(Boolean).join(" ");
    if (full) return full;
    const email = text(user?.email);
    return email ? email.split("@")[0] : fallback;
}
