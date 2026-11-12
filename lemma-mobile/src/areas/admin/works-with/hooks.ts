import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCacheKey } from "../../../data/hooks";
import { useMayI } from "../access/hooks";
import { adminKey, useAdminSource } from "../hooks";
import type { AboutLine } from "../types";
import { MANAGE_MEMBERS } from "@web/data/pod-roles";
import { worksWithLine, type LinkShare, type PodLink } from "./model";
import { everyPage } from "@web/space/pod-links";

/** This part's reads and writes, through react-query. */
export function useWorksWithSource() {
    return useAdminSource().worksWith;
}

function useKeyOf() {
    const key = useCacheKey();
    return (podId: string, ...rest: readonly unknown[]) => adminKey(key, "worksWith", podId, ...rest);
}

/** The Lemmas this one can ask. */
export function useAskable(podId: string) {
    const source = useWorksWithSource();
    const keyOf = useKeyOf();
    return useQuery({ queryKey: keyOf(podId, "askable"), queryFn: () => source.askable(podId), staleTime: 60_000 });
}

/** The Lemmas allowed to ask this one. */
export function useLinks(podId: string) {
    const source = useWorksWithSource();
    const keyOf = useKeyOf();
    return useQuery({ queryKey: keyOf(podId, "links"), queryFn: () => source.links(podId), staleTime: 60_000 });
}

/** Whether the signed-in person may let another Lemma ask this one, change
 *  what one reads, or stop it: `pod.member.manage` here, as the server says.
 *  Unknown counts as no. */
export function useMayConnect(podId: string) {
    return useMayI(podId, MANAGE_MEMBERS);
}

/** Every table here, every page of them, for choosing what to share. */
export function useShareableTables(podId: string) {
    const source = useWorksWithSource();
    const keyOf = useKeyOf();
    return useQuery({ queryKey: keyOf(podId, "shareable", "tables"), queryFn: () => everyPage((page) => source.shareable(podId, "tables", "/", page)) });
}

/** The root's listing and every folder somebody has opened, all of each, so
 *  a share can be any folder however deep. */
export function useShareableFolders(podId: string, paths: readonly string[]) {
    const source = useWorksWithSource();
    const keyOf = useKeyOf();
    return useQueries({
        queries: paths.map((path) => ({
            queryKey: keyOf(podId, "shareable", "files", path),
            queryFn: () => everyPage((page) => source.shareable(podId, "files", path, page)),
        })),
    });
}

/** Let another Lemma ask this one, or change what it reads. Both sides'
 *  lists are read again: this one's "asked by", and the other's "can ask". */
export function useConnect(podId: string) {
    const source = useWorksWithSource();
    const cache = useQueryClient();
    const keyOf = useKeyOf();
    return useMutation({
        mutationFn: ({ asker, shares }: { asker: string; shares: LinkShare[] }) => source.connect(podId, asker, shares),
        onSuccess: (_answer, { asker }) => {
            void cache.invalidateQueries({ queryKey: keyOf(podId, "links") });
            void cache.invalidateQueries({ queryKey: keyOf(asker, "askable") });
        },
    });
}

/** Stop another Lemma asking this one. */
export function useDisconnect(podId: string) {
    const source = useWorksWithSource();
    const cache = useQueryClient();
    const keyOf = useKeyOf();
    return useMutation({
        mutationFn: (link: PodLink) => source.disconnect(podId, link.podId),
        onSuccess: (_answer, link) => {
            void cache.invalidateQueries({ queryKey: keyOf(podId, "links") });
            void cache.invalidateQueries({ queryKey: keyOf(link.podId, "askable") });
        },
    });
}

/** The row's live line on a Lemma's Settings tab: "Asks 2 · asked by 1",
 *  or "Nobody yet". */
export function useWorksWithLine(podId: string): AboutLine {
    const askable = useAskable(podId);
    const links = useLinks(podId);
    return worksWithLine(askable.isSuccess ? askable.data.length : null, links.data?.length ?? 0);
}
