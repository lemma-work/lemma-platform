import type { Pod } from "@/data/types";
import { isForbidden, isMissing, isUnauthorized } from "@/session/auth-state";

type Lookup = { status: "pending" | "error" | "success"; data?: Pod | null; error?: unknown; isFetching: boolean };

/** `preferred` is the teammate last opened in this organization, and it only
 *  applies when the address names none. A link always wins over a habit. */
export function podAccess(id: string | null, listed: Pod[] | undefined, lookup: Lookup, preferred: string | null = null): {
    state: "ready" | "loading" | "denied" | "missing" | "error";
    pod: Pod | null;
} {
    if (!id) return { state: "ready", pod: listed?.find(candidate => candidate.id === preferred) ?? listed?.[0] ?? null };
    if (lookup.status === "pending" || (lookup.status === "error" && lookup.isFetching)) return { state: "loading", pod: null };
    if (lookup.status === "error") {
        const state = isForbidden(lookup.error) ? "denied" : isMissing(lookup.error) ? "missing" : isUnauthorized(lookup.error) ? "loading" : "error";
        return { state, pod: null };
    }
    return lookup.data?.id === id ? { state: "ready", pod: listed?.find(candidate => candidate.id === id) ?? lookup.data } : { state: "missing", pod: null };
}

/** The last teammate opened, per organization.
 *
 *  Per organization rather than one id, because switching organization is
 *  itself a trip to `/t` with no teammate named — one remembered id would be
 *  in the organization just left, match nothing in the one arrived at, and
 *  every switch would land on the first pod in the list again. */
export type LastPods = Record<string, string>;

export function readLastPods(raw: unknown): LastPods {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
    return Object.fromEntries(Object.entries(raw).filter((entry): entry is [string, string] => typeof entry[1] === "string"));
}

export function rememberPod(last: LastPods, orgId: string | null, podId: string | null): LastPods {
    if (!orgId || !podId || last[orgId] === podId) return last;
    return { ...last, [orgId]: podId };
}
