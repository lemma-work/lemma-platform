import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCacheKey } from "../../../data/hooks";
import { problemOf } from "../../admin/words";
import { moreKey, useMoreSource } from "../hooks";
import { isNameConflict, takenSentence } from "./model";
import type { StartRequest, StartedLemma } from "./source";

/** This part's reads and writes, through react-query. Every key is
 *  `moreKey(useCacheKey(), "begin", …)`; the Lemmas list is the app's own
 *  (`[cacheKey, "lemmas", orgId]`, `useLemmas` in `data/hooks.ts`). */
export function useBeginSource() {
    return useMoreSource().begin;
}

/** How a start finished. */
export interface Started extends StartedLemma {
    needsModel: boolean;
}

/** Make a Lemma (`POST /pods`), as the web's `createLemma` does: its face is
 *  set as it is made, so there is nothing to do after. */
export function useStartLemma(orgId: string | null) {
    const source = useBeginSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    return useMutation({
        mutationFn: async (request: StartRequest): Promise<Started> => {
            /* Asked alongside the start, read at the end. */
            const model = source.needsModel(request.orgId).catch(() => false);
            let started: StartedLemma;
            try {
                started = await source.start(request);
            } catch (failure) {
                if (isNameConflict(failure)) throw new Error(takenSentence(request.name));
                throw new Error(problemOf(failure, "I couldn’t be made just now. Try again."));
            }
            return { ...started, needsModel: await model };
        },
        onSettled: () => {
            if (orgId) void cache.invalidateQueries({ queryKey: [key, "lemmas", orgId] });
        },
    });
}

const joinKey = (key: string, podId: string) => moreKey(key, "begin", "join", podId);

/** Whether the signed-in person has asked to join this Lemma already. Not
 *  asked again on its own after a refusal: the answer would be the same. */
/** The organization a Lemma you can open is in, or null. */
export function useLemmaHome(podId: string) {
    const source = useBeginSource();
    const key = useCacheKey();
    return useQuery({ queryKey: [key, "more", "begin", "home", podId], queryFn: () => source.homeOf(podId), enabled: Boolean(podId), retry: false, staleTime: 60_000 });
}

export function useJoinStanding(podId: string) {
    const source = useBeginSource();
    const key = useCacheKey();
    return useQuery({ queryKey: joinKey(key, podId), queryFn: () => source.myJoinRequest(podId), enabled: Boolean(podId), retry: false });
}

/** Ask to join. An open door admits on the spot, and the Lemmas list is
 *  what decides whether it can be opened, so reading it again is walking in. */
export function useAskToJoin(podId: string) {
    const source = useBeginSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    return useMutation({
        mutationFn: () => source.askToJoin(podId),
        onSuccess: (request) => {
            cache.setQueryData(joinKey(key, podId), request);
            if (request.standing === "approved") void cache.invalidateQueries({ queryKey: [key, "lemmas"] });
        },
    });
}
