import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCacheKey } from "../../../data/hooks";
import { problemOf } from "../../admin/words";
import { moreKey, useMoreSource } from "../hooks";
import { castIcon, isNameConflict, setupSettled, takenSentence } from "./model";
import type { StartRequest, StartedLemma } from "./source";

/** This part's reads and writes, through react-query. Every key is
 *  `moreKey(useCacheKey(), "begin", …)`; the Lemmas list is the app's own
 *  (`[cacheKey, "lemmas", orgId]`, `useLemmas` in `data/hooks.ts`). */
export function useBeginSource() {
    return useMoreSource().begin;
}

/** The templates a new Lemma can start from, as the server ships them. */
export function useShelf() {
    const source = useBeginSource();
    const key = useCacheKey();
    return useQuery({ queryKey: moreKey(key, "begin", "shelf"), queryFn: () => source.templates(), staleTime: 30 * 60_000 });
}

/** A just-started Lemma's import, by Lemma, for as long as the app is open
 *  (the web keeps it for the tab): the strip over its chat reads it. */
const setups = new Map<string, string>();

/** How a start finished. */
export interface Started extends StartedLemma {
    /** False when the template's face could not be stored, so it kept its own. */
    faceSaved: boolean;
    needsModel: boolean;
}

/** Start a Lemma (`POST /pods/start`), then wear the template's face. A face
 *  that will not save never fails the start: a Lemma with the wrong face is
 *  a Lemma, and a second try would start a second one. */
export function useStartLemma(orgId: string | null) {
    const source = useBeginSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    return useMutation({
        mutationFn: async ({ request, face }: { request: StartRequest; face: string | null }): Promise<Started> => {
            /* Asked alongside the start, read at the end. */
            const model = source.needsModel(request.orgId).catch(() => false);
            let started: StartedLemma;
            try {
                started = await source.start(request);
            } catch (failure) {
                if ("name" in request && isNameConflict(failure)) throw new Error(takenSentence(request.name));
                throw new Error(problemOf(failure, "Couldn’t start it. Try again."));
            }
            let faceSaved = true;
            const icon = castIcon(face);
            if (icon && !started.standsIn) {
                try {
                    await source.setFace(started.id, icon);
                } catch {
                    faceSaved = false;
                }
            }
            if (started.importId) setups.set(started.standsIn?.id ?? started.id, started.importId);
            return { ...started, faceSaved, needsModel: await model };
        },
        onSettled: () => {
            if (orgId) void cache.invalidateQueries({ queryKey: [key, "lemmas", orgId] });
        },
    });
}

/** How far a just-started Lemma's template has got, while it is coming in:
 *  null once it is in, or when this app did not start it. Read every second
 *  and a half until it settles; a failed read hides the strip. */
export function useSetup(podId: string) {
    const source = useBeginSource();
    const key = useCacheKey();
    const [importId, setImportId] = useState(() => setups.get(podId) ?? null);
    useEffect(() => setImportId(setups.get(podId) ?? null), [podId]);
    const status = useQuery({
        queryKey: moreKey(key, "begin", "setup", podId, importId ?? ""),
        queryFn: () => source.setup(podId, importId as string),
        enabled: Boolean(importId),
        refetchInterval: (query) => (query.state.status === "error" || (query.state.data && setupSettled(query.state.data.status)) ? false : 1500),
        retry: false,
    });
    const done = status.data?.status === "COMPLETED";
    useEffect(() => {
        if (done) setups.delete(podId);
    }, [done, podId]);
    if (!importId || status.isError) return null;
    return status.data ?? null;
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
