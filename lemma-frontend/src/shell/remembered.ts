"use client";

import { useEffect } from "react";
import { source } from "@/data";
import { isLandingPreview } from "@/marketing/preview-mode";
import { key, type KeyValueStore } from "@/session/storage";

/** What the frame of the app looked like last time, kept in this browser.
 *
 *  The rail, the teammate's name and its chats are almost always what they
 *  were on the last visit, but drawing them waited on three requests in a
 *  row — the organizations, then the teammate, then its list. Read back from
 *  here they paint at once, and each query still asks the server straight
 *  away (`initialDataUpdatedAt: 0` makes what was remembered stale on
 *  arrival), so anything that did change replaces it a moment later.
 *
 *  It belongs to the account that wrote it: `retainWorkspaceOwner` wipes it,
 *  with the other workspace keys, when somebody else signs in or nobody is
 *  signed in at all. Live data only — the sample and the tour have nothing
 *  to wait for. */
export const REMEMBERED = "remembered";

/** Enough for the frames of several teammates, and no more. */
const KEEP = 24;

type Entry = { at: number; data: unknown };

function store(): KeyValueStore | null {
    try { return source.label === "live" && !isLandingPreview() ? localStorage : null; } catch { return null; }
}

function read(from: KeyValueStore): Record<string, Entry> {
    try {
        const raw: unknown = JSON.parse(from.getItem(key(REMEMBERED)) ?? "{}");
        return raw && typeof raw === "object" && !Array.isArray(raw) ? raw as Record<string, Entry> : {};
    } catch {
        return {};
    }
}

/** What was last seen under a query key, or undefined. */
export function recalled<T>(queryKey: readonly unknown[]): T | undefined {
    const from = store();
    return from ? read(from)[JSON.stringify(queryKey)]?.data as T | undefined : undefined;
}

/** Keep a query's answer for next time. Only an answer the server gave —
 *  `updatedAt` is 0 while the query still holds only what was recalled. */
export function useRemember(queryKey: readonly unknown[], data: unknown, updatedAt: number): void {
    const name = JSON.stringify(queryKey);
    const unnamed = queryKey.some((part) => part == null);
    useEffect(() => {
        const into = store();
        if (!into || !updatedAt || data === undefined || unnamed) return;
        const all = read(into);
        all[name] = { at: Date.now(), data };
        const kept = Object.fromEntries(Object.entries(all).sort((a, b) => b[1].at - a[1].at).slice(0, KEEP));
        try { into.setItem(key(REMEMBERED), JSON.stringify(kept)); } catch { /* Full or blocked: it is only a head start. */ }
    }, [name, unnamed, data, updatedAt]);
}
