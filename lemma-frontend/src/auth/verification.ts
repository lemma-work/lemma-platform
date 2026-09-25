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
