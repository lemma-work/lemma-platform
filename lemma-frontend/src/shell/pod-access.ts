import type { Pod } from "@/data/types";
import { isForbidden, isMissing, isUnauthorized } from "@/session/auth-state";

type Lookup = { status: "pending" | "error" | "success"; data?: Pod | null; error?: unknown; isFetching: boolean };

export function podAccess(id: string | null, listed: Pod[] | undefined, lookup: Lookup): {
    state: "ready" | "loading" | "denied" | "missing" | "error";
    pod: Pod | null;
} {
    if (!id) return { state: "ready", pod: listed?.[0] ?? null };
    if (lookup.status === "pending" || (lookup.status === "error" && lookup.isFetching)) return { state: "loading", pod: null };
    if (lookup.status === "error") {
        const state = isForbidden(lookup.error) ? "denied" : isMissing(lookup.error) ? "missing" : isUnauthorized(lookup.error) ? "loading" : "error";
        return { state, pod: null };
    }
    return lookup.data?.id === id ? { state: "ready", pod: listed?.find(candidate => candidate.id === id) ?? lookup.data } : { state: "missing", pod: null };
}
