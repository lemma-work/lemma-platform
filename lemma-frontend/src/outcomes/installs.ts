"use client";

import { useCallback, useEffect, useMemo } from "react";
import { useQueries, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { sourceOfInstall } from "@/arrival/install-plan";
import { runOf, type ServerInstall } from "@/arrival/install-state";
import type { RunKind } from "@/arrival/stage-model";
import { lemma } from "@/session/client";
import { key } from "@/session/storage";

/* Apps going into a Lemma from the Add an app page, kept where every view
   of the Lemma can read them: the page itself, its spot in the sidebar and
   the ring on its scene. Each Add press queues one install on the server
   (`POST /pods/{pod}/bundle/installs`), which carries on whether or not
   anybody watches, and the Lemma says so in the reader's chat with it when
   it is in. What is kept here is only which installs this browser started,
   so that leaving the page, or reloading it, does not lose sight of them.

   Held in the query cache under one key, and in this browser's storage
   beside it; each install's state is read from the server under a key of
   its own, every few seconds until it stops moving. */

/** An app on its way into a Lemma from the Add an app page. */
export interface GoingIn {
    /** The plugin's slug, which is also its glyph. */
    slug: string;
    name: string;
    kind: RunKind;
    /** How far along, 0 to 1. */
    progress: number;
    /** The app it put in the Lemma, once in. */
    app: string | null;
    reason: string | null;
}

/** One app going in, as the Add page tells it: what the sidebar knows, and
 *  the step of its plan under way, for the words under its ring. */
export interface AddRow extends GoingIn {
    /** The plan step running now, by kind: `APP`, `TABLE`, `FUNCTION`… */
    step: string | null;
}

/** What an Add press needs of a plugin: who it is, and where it installs
 *  from (`/import/github/<owner>/<repo>?path=…`). */
export interface Addable {
    slug: string;
    name: string;
    install: string | null;
}

/** One Add press, as this browser remembers it. */
interface Started {
    podId: string;
    slug: string;
    name: string;
    install: string;
    /** When it was pressed. */
    at: number;
    /** The server's id for the install; null until it answers, and when it
     *  would not take it. */
    installId: string | null;
    /** Why the server would not take the last thing asked of it, in its
     *  own words. */
    refused: string | null;
    /** How it stopped moving, and when: kept a little while after, so the
     *  person sees it land, then dropped. */
    settled: "in" | "stopped" | null;
    settledAt: number | null;
}

const STORE = key("outcomes-going-in");
const STARTED = ["going-in-started"] as const;
const STATUS = "going-in-status";
/** How often an install still moving is asked about. */
const POLL_MS = 2500;
/** An app that went in shows as just-in this long, then is one of the
 *  Lemma's apps like any other. */
const KEEP_IN_MS = 3 * 60_000;
/** One that stopped keeps its reason in view longer: somebody may want to
 *  try again. */
const KEEP_STOPPED_MS = 30 * 60_000;
/** One never heard to settle in this long is one the server lost. */
const KEEP_UNSETTLED_MS = 6 * 60 * 60_000;

const NONE: readonly Started[] = [];

function isStarted(value: unknown): value is Started {
    if (!value || typeof value !== "object") return false;
    const one = value as Record<string, unknown>;
    const text = (field: unknown) => field === null || typeof field === "string";
    return typeof one.podId === "string" && typeof one.slug === "string" && typeof one.name === "string"
        && typeof one.install === "string" && typeof one.at === "number"
        && text(one.installId) && text(one.refused)
        && (one.settled === null || one.settled === "in" || one.settled === "stopped")
        && (one.settledAt === null || typeof one.settledAt === "number");
}

/** What this browser kept, read defensively: storage can be off, full, or
 *  hold what an older build wrote. */
function readStored(): Started[] {
    try {
        const raw = localStorage.getItem(STORE);
        const parsed: unknown = raw ? JSON.parse(raw) : [];
        return Array.isArray(parsed) ? parsed.filter(isStarted) : [];
    } catch {
        return [];
    }
}

/** Change what is kept, in the cache every view reads and in storage, so
 *  a reload picks up where this left off. */
function change(client: QueryClient, edit: (was: readonly Started[]) => Started[]): void {
    const next = edit(client.getQueryData<Started[]>(STARTED) ?? readStored());
    client.setQueryData(STARTED, next);
    try {
        if (next.length > 0) localStorage.setItem(STORE, JSON.stringify(next));
        else localStorage.removeItem(STORE);
    } catch {
        /* kept for this visit only */
    }
}

/** When a kept install stops being worth showing. */
function endOf(one: Started): number {
    if (one.settledAt === null) return one.at + KEEP_UNSETTLED_MS;
    return one.settledAt + (one.settled === "in" ? KEEP_IN_MS : KEEP_STOPPED_MS);
}

function installsPath(podId: string): string {
    return `/pods/${encodeURIComponent(podId)}/bundle/installs`;
}

function statusKey(podId: string, installId: string | null): readonly unknown[] {
    return [STATUS, podId, installId];
}

/** The server's words for why it said no, or plain ones of ours. */
function wordsOf(failure: unknown, fallback: string): string {
    return failure instanceof Error && failure.message.trim() ? failure.message : fallback;
}

/** A Lemma that just gained an app has new things everywhere -- the app,
 *  and the tables, automations and agents it brought -- so everything read
 *  about it is read again: its app list under the key every view shares
 *  (`["outcome-apps", pod]`), the workspace's (`["tabs", pod]`), and the
 *  rest it is the second word of. */
function refreshLemma(client: QueryClient, podId: string): void {
    void client.invalidateQueries({ predicate: (query) => query.queryKey[1] === podId && query.queryKey[0] !== STATUS });
}

/** Every install this browser started, from the cache, read from storage
 *  once. */
function useStarted(): readonly Started[] {
    const started = useQuery({ queryKey: STARTED, queryFn: readStored, staleTime: Infinity, gcTime: Infinity });
    return started.data ?? NONE;
}

/** One Lemma's installs and where each is, polled while any is moving.
 *  Every view that asks shares the same reads, so the sidebar, the scene and
 *  the Add page never disagree. Also settles what stopped moving -- making
 *  the Lemma's lists read again when an app lands -- and drops what has
 *  been shown long enough. */
function usePodInstalls(podId: string | null): { mine: readonly Started[]; rows: readonly AddRow[] } {
    const client = useQueryClient();
    const all = useStarted();
    const mine = useMemo(() => all.filter((one) => one.podId === podId), [all, podId]);
    const statuses = useQueries({
        queries: mine.map((one) => ({
            queryKey: statusKey(one.podId, one.installId),
            queryFn: () => lemma(one.podId).request<ServerInstall>("GET", `${installsPath(one.podId)}/${encodeURIComponent(one.installId ?? "")}`),
            enabled: one.installId !== null,
            refetchInterval: (query: { state: { data?: ServerInstall } }) => (query.state.data?.finished ? false : POLL_MS),
            staleTime: (query: { state: { data?: ServerInstall } }) => (query.state.data?.finished ? Infinity : 0),
            retry: 1,
        })),
    });
    /* A new array each render; when each was last read says when any of
       them changed. */
    const readAt = statuses.map((one) => `${one.dataUpdatedAt}:${one.errorUpdatedAt}`).join(",");
    const rows = useMemo<AddRow[]>(
        () => mine.map((one, index) => {
            const status = statuses[index];
            const data = status?.data ?? null;
            const lost = status?.isError && !data ? "I can’t see how it’s going right now." : null;
            const row = runOf([{ slug: one.slug, install: one.install }], data, one.refused ?? lost)[one.slug];
            return { slug: one.slug, name: one.name, kind: row.kind, progress: row.progress, step: row.step, app: row.app, reason: row.reason };
        }),
        /* `statuses` is read through `readAt`, which changes when it does. */
        [mine, readAt],
    );

    /* What stopped moving is settled once, by whichever view sees it first:
       an app that landed makes everything about its Lemma read again. One
       sent back to try again is moving again. */
    useEffect(() => {
        const settle = new Map<string, Started["settled"]>();
        mine.forEach((one, index) => {
            const data = statuses[index]?.data;
            if (!data || !one.installId) return;
            if (data.finished && one.settled === null) settle.set(one.installId, rows[index]?.kind === "added" ? "in" : "stopped");
            if (!data.finished && one.settled !== null) settle.set(one.installId, null);
        });
        if (settle.size === 0) return;
        const now = Date.now();
        /* Against what is kept now, not this render's copy: another view may
           have settled it a moment ago, and the Lemma is read again once. */
        let landed = false;
        change(client, (was) => was.map((one) => {
            if (!one.installId || !settle.has(one.installId)) return one;
            const settled = settle.get(one.installId) ?? null;
            if (one.settled === settled) return one;
            if (settled === "in") landed = true;
            return { ...one, settled, settledAt: settled ? now : null };
        }));
        if (landed && podId) refreshLemma(client, podId);
    }, [readAt, mine, client, podId]);

    /* Dropped when they have been shown long enough: now, or at the next
       moment one is due. */
    useEffect(() => {
        if (all.length === 0) return;
        const prune = () => change(client, (was) => was.filter((one) => endOf(one) > Date.now()));
        const soonest = Math.min(...all.map(endOf));
        const wait = soonest - Date.now();
        if (wait <= 0) {
            prune();
            return;
        }
        const timer = window.setTimeout(prune, wait + 50);
        return () => window.clearTimeout(timer);
    }, [all, client]);

    return { mine, rows };
}

/** The apps going into a Lemma right now, and the ones that just went in. */
export function useGoingIn(podId: string | null): readonly GoingIn[] {
    return usePodInstalls(podId).rows;
}

/** Adding apps to one Lemma: each plugin's row by slug, a press that
 *  queues one on the server, and trying again on one that stopped. The
 *  row appears the moment Add is pressed, waiting, and moves as the server
 *  answers. */
export function useAddPlugin(podId: string): {
    rows: ReadonlyMap<string, AddRow>;
    add: (plugin: Addable) => void;
    retry: (slug: string) => Promise<void>;
} {
    const client = useQueryClient();
    const { mine, rows } = usePodInstalls(podId);
    const bySlug = useMemo(() => new Map(rows.map((row) => [row.slug, row])), [rows]);

    const add = useCallback((plugin: Addable) => {
        const source = plugin.install ? sourceOfInstall(plugin.install) : null;
        if (!plugin.install || !source) return;
        const at = Date.now();
        const press = (one: Started) => one.podId === podId && one.slug === plugin.slug && one.at === at;
        change(client, (was) => [
            ...was.filter((one) => !(one.podId === podId && one.slug === plugin.slug)),
            { podId, slug: plugin.slug, name: plugin.name, install: plugin.install as string, at, installId: null, refused: null, settled: null, settledAt: null },
        ]);
        lemma(podId)
            .request<ServerInstall>("POST", installsPath(podId), {
                body: { plugins: [{ owner: source.owner, repo: source.repo, path: source.path }] },
            })
            .then((made) => {
                client.setQueryData(statusKey(podId, made.install_id), made);
                change(client, (was) => was.map((one) => (press(one) ? { ...one, installId: made.install_id } : one)));
            })
            .catch((failure: unknown) => {
                const refused = wordsOf(failure, "It didn’t start.");
                change(client, (was) => was.map((one) => (press(one) ? { ...one, refused, settled: "stopped", settledAt: Date.now() } : one)));
            });
    }, [client, podId]);

    const retry = useCallback(async (slug: string) => {
        const one = mine.find((kept) => kept.slug === slug);
        if (!one) return;
        /* Never queued: ask again. */
        if (!one.installId) {
            add(one);
            return;
        }
        const installId = one.installId;
        const which = (kept: Started) => kept.podId === podId && kept.installId === installId;
        /* Queued, but its state could not be read: read it again. */
        if (!client.getQueryData(statusKey(podId, installId))) {
            await client.refetchQueries({ queryKey: statusKey(podId, installId) });
            return;
        }
        /* Stopped on the server: it carries on from the step that failed. */
        try {
            const again = await lemma(podId).request<ServerInstall>("POST", `${installsPath(podId)}/${encodeURIComponent(installId)}/retry`);
            client.setQueryData(statusKey(podId, installId), again);
            change(client, (was) => was.map((kept) => (which(kept) ? { ...kept, refused: null, settled: null, settledAt: null } : kept)));
        } catch (failure) {
            const refused = wordsOf(failure, "It didn’t start again.");
            change(client, (was) => was.map((kept) => (which(kept) ? { ...kept, refused, settled: "stopped", settledAt: Date.now() } : kept)));
        }
    }, [add, client, mine, podId]);

    return { rows: bySlug, add, retry };
}
