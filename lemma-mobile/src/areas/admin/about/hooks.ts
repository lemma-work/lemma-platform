import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCacheKey } from "../../../data/hooks";
import type { PickedFile } from "../../../data/io";
import type { Lemma } from "../../../data/types";
import { adminKey, useAdminSource } from "../hooks";

export function useAboutSource() {
    return useAdminSource().about;
}

export function useOrigin(podId: string) {
    const source = useAboutSource();
    const key = useCacheKey();
    return useQuery({ queryKey: adminKey(key, "about", podId, "origin"), queryFn: () => source.origin(podId), staleTime: 10 * 60_000 });
}

/** Change the Lemmas list the rest of the app reads (`[key, "lemmas", org]`)
 *  the moment a write lands, then read it again: the list, a Lemma's page
 *  and its face all say the new thing at once. */
function usePatchLemmas() {
    const cache = useQueryClient();
    const key = useCacheKey();
    return (podId: string, patch: Partial<Lemma> | null) => {
        cache.setQueriesData<Lemma[]>({ queryKey: [key, "lemmas"] }, (held) =>
            held ? (patch === null ? held.filter((one) => one.id !== podId) : held.map((one) => (one.id === podId ? { ...one, ...patch } : one))) : held);
        void cache.invalidateQueries({ queryKey: [key, "lemmas"] });
    };
}

/** Save a new name and what it is for: only what changed is sent. */
export function useSaveAbout(lemma: Lemma) {
    const source = useAboutSource();
    const patch = usePatchLemmas();
    return useMutation({
        mutationFn: async ({ name, description }: { name: string; description: string }) => {
            const nextName = name.trim();
            if (nextName && nextName !== lemma.name) await source.rename(lemma.id, nextName);
            if (description.trim() !== (lemma.description ?? "").trim()) await source.describe(lemma.id, description);
            return { name: nextName || lemma.name, description: description.trim() || null };
        },
        onSuccess: (saved) => patch(lemma.id, saved),
    });
}

/** Give a Lemma a face: a picture from the phone (uploaded first), a
 *  character, or its own again (null). */
export function useSetFace(podId: string) {
    const source = useAboutSource();
    const patch = usePatchLemmas();
    return useMutation({
        mutationFn: async (face: { picture: PickedFile } | { icon: string | null }) => {
            const icon = "picture" in face ? await source.uploadIcon(face.picture) : face.icon;
            await source.setIcon(podId, icon);
            return icon;
        },
        onSuccess: (icon) => patch(podId, { iconUrl: icon }),
    });
}

/** Delete a Lemma. Its own reads are dropped rather than read again: they
 *  would only come back as not found. */
export function useDeleteLemma(podId: string) {
    const source = useAboutSource();
    const patch = usePatchLemmas();
    const cache = useQueryClient();
    const key = useCacheKey();
    return useMutation({
        mutationFn: () => source.remove(podId),
        onSuccess: () => {
            patch(podId, null);
            cache.removeQueries({ queryKey: adminKey(key, "access", podId) });
            cache.removeQueries({ queryKey: adminKey(key, "about", podId) });
        },
    });
}
