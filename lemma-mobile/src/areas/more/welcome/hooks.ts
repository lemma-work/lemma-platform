import { useCallback } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCacheKey, useOrg } from "../../../data/hooks";
import type { Org } from "../../../data/types";
import { useSource } from "../../../session/session";
import { moreKey, useMoreSource } from "../hooks";
import type { NewOrg } from "./source";

/** This part's reads and writes, through react-query. Every key is
 *  `moreKey(useCacheKey(), "welcome", …)`; the organization list is the
 *  app's own (`[cacheKey, "orgs"]`, `useOrg` in `data/hooks.ts`). */
export function useWelcomeSource() {
    return useMoreSource().welcome;
}

const keys = {
    invitations: (key: string) => moreKey(key, "welcome", "invitations"),
    suggested: (key: string) => moreKey(key, "welcome", "suggested"),
    invitation: (key: string, id: string) => moreKey(key, "welcome", "invitation", id),
};

/** Pending invitations to whoever is signed in. */
export function useInvitations() {
    const source = useWelcomeSource();
    const key = useCacheKey();
    return useQuery({ queryKey: keys.invitations(key), queryFn: () => source.myInvitations(), staleTime: 30_000 });
}

/** Organizations this person's email domain may join. */
export function useSuggested() {
    const source = useWelcomeSource();
    const key = useCacheKey();
    return useQuery({ queryKey: keys.suggested(key), queryFn: () => source.suggestedOrgs(), staleTime: 30_000 });
}

/** One invitation, read from its link. A read the server refused is not
 *  asked again on its own: the answer would be the same. */
export function useInvitation(id: string) {
    const source = useWelcomeSource();
    const key = useCacheKey();
    return useQuery({ queryKey: keys.invitation(key, id), queryFn: () => source.invitation(id), enabled: Boolean(id), retry: false });
}

/** After any way in (an invitation, a domain, a new one): read the
 *  organizations again, and choose the one entered when the list has it.
 *  Answers whether it did, so sample data, whose list never changes, can
 *  say so. Never chooses an organization the list does not hold, since the
 *  choice is kept on the phone for every later launch, and a sample
 *  organization's id would then be chosen for a live account. */
export function useArrive() {
    const source = useSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    const { pick } = useOrg();
    return useCallback(async (orgId: string | null): Promise<boolean> => {
        const orgs = await cache.fetchQuery<Org[]>({ queryKey: [key, "orgs"], queryFn: () => source.orgs(), staleTime: 0 });
        void cache.invalidateQueries({ queryKey: moreKey(key, "welcome") });
        /* An invitation can bring somebody into a Lemma of an organization
           they were in already, so its list is read again too. */
        void cache.invalidateQueries({ queryKey: [key, "lemmas"] });
        const listed = Boolean(orgId && orgs.some((one) => one.id === orgId));
        if (listed && orgId) pick(orgId);
        return listed;
    }, [cache, key, pick, source]);
}

/** Make an organization, and arrive in it. */
export function useCreateOrg() {
    const source = useWelcomeSource();
    const arrive = useArrive();
    return useMutation({
        mutationFn: async (wanted: NewOrg) => {
            const made = await source.createOrg(wanted);
            const listed = await arrive(made.id);
            return { org: made, listed };
        },
    });
}

/** Accept or decline an invitation from its link. Accepting arrives in the
 *  organization; either way the invitation is read again. */
export function useDecide(id: string) {
    const source = useWelcomeSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    const arrive = useArrive();
    return useMutation({
        mutationFn: async ({ decision, orgId }: { decision: "accept" | "reject"; orgId: string }) => {
            if (decision === "reject") {
                await source.declineInvitation(id);
                return { decision, redirectUri: null, listed: false };
            }
            const { redirectUri } = await source.acceptInvitation(id);
            const listed = await arrive(orgId);
            return { decision, redirectUri, listed };
        },
        onSettled: () => void cache.invalidateQueries({ queryKey: keys.invitation(key, id) }),
    });
}
