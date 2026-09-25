import type { Access } from "./completion";

export type VerificationPhase = "checking" | "inbox" | "done" | "expired" | "signed-out" | "problem";

export interface VerificationActions {
    access: () => Promise<Access>;
    verify: () => Promise<{ status: "OK" | "EMAIL_VERIFICATION_INVALID_TOKEN_ERROR" }>;
    send: () => Promise<{ status: "OK" | "EMAIL_ALREADY_VERIFIED_ERROR" }>;
    refresh: () => Promise<boolean>;
}

export async function startVerification(
    hasToken: boolean,
    actions: VerificationActions,
    recentlySent = false,
): Promise<VerificationPhase> {
    if (hasToken) {
        const result = await actions.verify();
        if (result.status !== "OK") return "expired";
        // Verification can succeed in a new browser without a session to refresh.
        await actions.refresh();
        return "done";
    }
    const access = await actions.access();
    if (access === "ready") return "done";
    if (access === "signed-out") return "signed-out";
    if (recentlySent) return "inbox";
    const result = await actions.send();
    if (result.status === "EMAIL_ALREADY_VERIFIED_ERROR") {
        await actions.refresh();
        return "done";
    }
    return "inbox";
}

/** One tick of the inbox poll.
 *
 *  Verified is the end of polling, whatever the refresh says. The refresh is
 *  what carries the new claim into the session, and it used to sit inside the
 *  poll with "ready" as the only way out — so an account whose access check
 *  still said otherwise refreshed the session every three seconds, and on
 *  every focus, for as long as the tab stayed open. Once is the attempt; the
 *  Continue button on the next screen is the retry, one per click. */
export async function checkInbox(
    actions: Pick<VerificationActions, "refresh"> & { verified: () => Promise<boolean> },
): Promise<"inbox" | "done"> {
    if (!(await actions.verified())) return "inbox";
    await actions.refresh().catch(() => false);
    return "done";
}
