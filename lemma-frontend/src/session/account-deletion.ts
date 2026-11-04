import { MATES, ORG } from "@/copy";

/** Deleting your own account, said before it happens and read after it is refused.
 *
 *  Pure, so the words the dialog shows and the public deletion page shows are
 *  one list, and so what a refusal turns into can be tested without a page.
 *  The order is the order it matters to somebody deciding: what goes, what
 *  stays with other people, what goes with you, and that it cannot be undone. */

/** Where a deletion leaves the browser: the public page, saying it is done.
 *  A page anyone can open, because by then nobody is signed in. */
export const DELETED_PATH = "/delete-account?deleted=1";

/** What this tab writes down once the API has said the account is gone.
 *
 *  The address alone is not proof — anybody can open it, signed in or not,
 *  and would be told an account that still exists has been deleted. So the
 *  page says so only when this tab also carries the note, which it writes
 *  after the answer and before it navigates. Per tab, like the answer was. */
const DELETION_NOTE = "lemma:account-deleted";

interface NoteStore {
    getItem(key: string): string | null;
    setItem(key: string, value: string): void;
}

/** Note that the API confirmed the deletion. A tab that cannot store it
 *  lands on the ordinary page instead, which is wrong only in its welcome. */
export function noteDeletionConfirmed(store: NoteStore): void {
    try { store.setItem(DELETION_NOTE, "1"); } catch { /* no storage */ }
}

/** Whether to show "your account has been deleted": the address says so,
 *  and this tab was the one that deleted it. */
export function isDeletionConfirmed(search: string, store: NoteStore): boolean {
    if (new URLSearchParams(search).get("deleted") !== "1") return false;
    try { return store.getItem(DELETION_NOTE) === "1"; } catch { return false; }
}

/** What deleting an account does, one sentence each. */
export const DELETION_CONSEQUENCES: readonly string[] = [
    "Your name, email address, phone number and time zone are erased, and you are signed out everywhere: browsers, the phone and desktop apps, and the command line.",
    `You leave every ${ORG} you are in. What you made or said with other people there — messages, pages, tables, files and ${MATES.toLowerCase()} — stays with them, shown as by “Deleted user”.`,
    `Any ${ORG} only you are in, such as your own, is deleted with its ${MATES.toLowerCase()} and everything they keep.`,
    "Accounts you connected are disconnected, and computers you paired stop working for you.",
    "This cannot be undone. You can sign up again with the same email address later, as a new, empty account.",
];

/** Whether what was typed is this account's address: the same address, in
 *  whatever case, with stray spaces around it. The API compares the same way. */
export function confirmsAccount(typed: string, email: string): boolean {
    const wanted = email.trim().toLowerCase();
    return wanted.length > 0 && typed.trim().toLowerCase() === wanted;
}

export interface DeletionRefusal {
    /** What to show, in a sentence the person can act on. */
    message: string;
    /** The organizations that need another owner first, when that is the reason. */
    organizations: string[];
    /** The account was already gone — a second click, a second tab. Not a
     *  failure: the person should be shown that it is deleted. */
    alreadyDeleted: boolean;
}

interface Problem {
    statusCode?: unknown;
    code?: unknown;
    message?: unknown;
    details?: unknown;
}

function namesIn(details: unknown): string[] {
    if (!details || typeof details !== "object") return [];
    const listed = (details as { organizations?: unknown }).organizations;
    if (!Array.isArray(listed)) return [];
    return listed
        .map((entry) => (entry && typeof entry === "object" ? (entry as { name?: unknown }).name : null))
        .filter((name): name is string => typeof name === "string" && name.length > 0);
}

/** What a failed deletion tells the person.
 *
 *  The API's refusals carry a message written to be shown — whose ownership
 *  to hand on, what subscription to cancel — so it is shown as it came. The
 *  rest is a request that did not get an answer, and says so without
 *  claiming to know whether anything changed. */
export function deletionRefusal(problem: unknown): DeletionRefusal {
    const { statusCode, code, message, details } = (problem ?? {}) as Problem;
    const said = typeof message === "string" && message.trim() ? message.trim() : null;
    if (code === "ACCOUNT_INACTIVE") {
        return { message: "This account has already been deleted.", organizations: [], alreadyDeleted: true };
    }
    if (statusCode === 401) {
        return { message: "Your session has ended. Sign in again, then try again.", organizations: [], alreadyDeleted: false };
    }
    if (code === "ACCOUNT_DELETION_CONFIRMATION_MISMATCH") {
        return { message: "That is not this account’s email address. Type it exactly as shown.", organizations: [], alreadyDeleted: false };
    }
    if (code === "ACCOUNT_DELETION_SOLE_OWNER") {
        return {
            message: said ?? `Make someone else an owner of your ${ORG} first.`,
            organizations: namesIn(details),
            alreadyDeleted: false,
        };
    }
    if (said && typeof statusCode === "number" && statusCode >= 400 && statusCode < 500) {
        return { message: said, organizations: [], alreadyDeleted: false };
    }
    if (code === "ACCOUNT_DELETION_UNAVAILABLE" && said) {
        return { message: said, organizations: [], alreadyDeleted: false };
    }
    return {
        message: "We couldn’t confirm your account was deleted. Check your connection and try again.",
        organizations: [],
        alreadyDeleted: false,
    };
}
