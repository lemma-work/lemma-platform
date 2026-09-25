import { onApi, PORTAL_PATH } from "./config";
import { authLink, landing, pendingDestination, rememberDestination } from "./redirects";

export type Access = "ready" | "verify" | "signed-out";

function isRecord(value: unknown): value is Record<string, unknown> {
    return typeof value === "object" && value !== null;
}

export function needsVerification(body: unknown): boolean {
    if (!isRecord(body)) return false;
    if (isRecord(body.detail) && body.detail.code === "EMAIL_VERIFICATION_REQUIRED") return true;
    return Array.isArray(body.claimValidationErrors) && body.claimValidationErrors.some(
        (claim: unknown) => isRecord(claim) && claim.id === "st-ev",
    );
}

/** Use the API's policy, including deployments where verification is disabled. */
export async function accountAccess(fetcher: typeof fetch = fetch): Promise<Access> {
    const response = await fetcher(onApi("/users/me"), { credentials: "include", cache: "no-store" });
    if (response.ok) return "ready";
    if (response.status === 401) return "signed-out";
    if (response.status === 403 && needsVerification(await response.json().catch(() => null))) return "verify";
    throw new Error(response.status === 403
        ? "This account cannot access Lemma. Contact your administrator."
        : "We couldn’t check your account. Try again.");
}

export function completionDestination(access: Access, search: string): string {
    if (access === "ready") return landing(search);
    rememberDestination(pendingDestination(search));
    return authLink(access === "verify" ? PORTAL_PATH + "/verify-email" : PORTAL_PATH, search);
}

export async function completeAuth(search = window.location.search): Promise<void> {
    const access = await accountAccess();
    if (access === "signed-out") throw new Error("Your session could not be confirmed. Please sign in again.");
    window.location.replace(completionDestination(access, search));
}
