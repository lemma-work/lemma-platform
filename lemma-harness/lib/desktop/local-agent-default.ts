import { RuntimeProfileKind, RuntimeProfileStatus } from "lemma-sdk";

/**
 * Which coding agent a freshly created pod should answer with.
 *
 * A pod with no default runtime falls back to the installation provider, which
 * on a local install is usually unconfigured — the first message then dies on
 * "No LLM model is configured on this server" and the pod is useless from the
 * moment it is made. So a local install that has an agent adopts it.
 *
 * Whichever was created first, deterministically. With several to choose from,
 * the composer's picker is where someone changes their mind; what must not
 * happen is the pod opening with no default at all.
 */
export type AdoptableProfile = {
    id: string;
    name: string;
    kind?: string | null;
    status?: string | null;
    created_at?: string | null;
    availability_status?: string | null;
};

export function isReadyLocalAgent(profile: AdoptableProfile): boolean {
    return profile.kind === RuntimeProfileKind.HARNESS
        && profile.status === RuntimeProfileStatus.ACTIVE
        && profile.availability_status === "READY";
}

export function adoptableLocalAgent<T extends AdoptableProfile>(
    profiles: readonly T[] | null | undefined,
): T | null {
    const agents = (profiles ?? []).filter(isReadyLocalAgent);
    if (agents.length < 2) return agents[0] ?? null;
    // The listing's order is the server's, not a promise. Sorting by creation
    // keeps "the agent they set up first" stable across refetches, so two runs
    // of onboarding cannot adopt different agents from the same list.
    return [...agents].sort((left, right) =>
        (left.created_at ?? "").localeCompare(right.created_at ?? "")
        || left.id.localeCompare(right.id),
    )[0];
}
