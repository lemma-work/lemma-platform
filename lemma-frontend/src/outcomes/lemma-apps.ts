"use client";

import { useMemo } from "react";
import { useQueries } from "@tanstack/react-query";
import { source, type Pod, type Tab } from "@/data";
import { homeFirst } from "./model";

export type AppTab = Extract<Tab, { kind: "app" }>;

/** A Lemma's apps, the one it opens on first. That is the app somebody chose
 *  (`home`, the Lemma's `homeApp`) when it still has it; otherwise the list's
 *  own order, oldest first, which nobody chose. */
export function appsOf(tabs: readonly Tab[] | undefined, home?: string): AppTab[] {
    const apps = (tabs ?? []).flatMap((tab) => (tab.kind === "app" ? [tab] : []));
    return homeFirst(apps, home, (app) => app.id);
}

/** The name an app is addressed by: its tab's id without the kind. */
export function appName(app: AppTab): string {
    return app.id.replace(/^app:/, "");
}

/** The apps of the Lemmas given, by Lemma, read under the key every view of
 *  them shares. Asked for the Lemmas that show theirs, not for all of them:
 *  a folded Lemma is a name, and a read apiece to draw a list of names is
 *  what the sidebar already declines to pay for chats. */
export function useLemmaApps(pods: readonly Pod[]): ReadonlyMap<string, readonly AppTab[]> {
    const results = useQueries({
        queries: pods.map((pod) => ({ queryKey: ["outcome-apps", pod.id], queryFn: () => source.listTabs(pod.id), staleTime: 5 * 60_000 })),
    });
    /* A new array each render; when each list was last read says when any of
       them changed. */
    const readAt = results.map((one) => one.dataUpdatedAt).join(",");
    return useMemo(
        () => new Map(pods.map((pod, index) => [pod.id, appsOf(results[index]?.data, pod.homeApp)])),
        /* `results` is read through `readAt`, which changes when it does. */
        [pods, readAt],
    );
}
