import { useQuery } from "@tanstack/react-query";
import { useApps, useCacheKey } from "../../../data/hooks";
import { useSession } from "../../../session/session";
import { useFilesKey, useFilesSource } from "../../files/hooks";
import { moreKey, useMoreSource } from "../hooks";
import { addressOf } from "./model";
import type { ShareSubject } from "./source";

/** Share's reads, through react-query. The people are settings' own hooks
 *  (`usePodMembers`, `useMayManage`); a file's links and address are the
 *  files area's reads under its keys, so this sheet and the file's own link
 *  sheet count the same links. */
export function useShareSource() {
    return useMoreSource().share;
}

/** Who can open a file, a table or an app. Nothing for a Lemma, whose
 *  reach is its people. */
export function useReach(podId: string, subject: ShareSubject, enabled: boolean) {
    const source = useShareSource();
    const key = useCacheKey();
    const thing = subject.kind === "lemma" ? null : subject;
    return useQuery({
        queryKey: moreKey(key, "share", podId, "reach", thing?.kind ?? "lemma", thing ? (thing.kind === "file" ? thing.path : thing.name) : ""),
        queryFn: () => source.reach(podId, thing!),
        enabled: enabled && thing !== null,
        staleTime: 60_000,
        retry: 1,
    });
}

/** A file's public links still working, under the files area's key. */
export function useLiveLinks(podId: string, path: string, enabled: boolean) {
    const source = useFilesSource();
    const keyOf = useFilesKey(podId);
    const links = useQuery({ queryKey: keyOf("links", path), queryFn: () => source.links(podId, path), enabled: enabled && Boolean(path) });
    const now = Date.now();
    return { live: (links.data ?? []).filter((one) => Date.parse(one.expiresAt) > now).length, links };
}

/** The address handed out for the subject. `own` when it is the thing's
 *  own address rather than the web's page for it (an app's); `state` says
 *  why there is none yet: still being read, the server gave none, or the
 *  read failed (with `retry`). */
export interface ShareAddress {
    url: string | null;
    own: boolean;
    state: "ready" | "reading" | "none" | "failed";
    retry: () => void;
}

export function useShareAddress(podId: string, subject: ShareSubject, enabled: boolean): ShareAddress {
    const { server } = useSession();
    const files = useFilesSource();
    const keyOf = useFilesKey(podId);
    const path = subject.kind === "file" ? subject.path : "";
    const inside = useQuery({ queryKey: keyOf("app-url", path), queryFn: () => files.appUrl(podId, path), enabled: enabled && Boolean(path), staleTime: 10 * 60_000 });
    const { apps, pending } = useApps();
    const retry = () => void inside.refetch();
    if (subject.kind === "app") {
        if (pending) return { url: null, own: false, state: "reading", retry };
        const own = apps.find((app) => app.podId === podId && app.name === subject.name)?.url ?? null;
        return { url: addressOf(subject, { web: server.web, podId, appUrl: own }), own: Boolean(own), state: "ready", retry };
    }
    if (subject.kind === "file") {
        const url = addressOf(subject, { web: server.web, podId, fileUrl: inside.data ?? null });
        const state = inside.isError ? "failed" : inside.isPending ? "reading" : url ? "ready" : "none";
        return { url, own: false, state, retry };
    }
    return { url: addressOf(subject, { web: server.web, podId }), own: false, state: "ready", retry };
}
