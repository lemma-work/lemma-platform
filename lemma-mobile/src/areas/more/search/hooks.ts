import { useEffect, useMemo, useState } from "react";
import { useMutation, useQueries, type UseQueryResult } from "@tanstack/react-query";
import { useCacheKey, useLemmas } from "../../../data/hooks";
import type { ConversationRef } from "../../../data/types";
import { adminKey } from "../../admin/hooks";
import { usePeopleSource } from "../../admin/people/hooks";
import { useChatsSource } from "../../chats/hooks";
import { tableKeys, useTablesSource } from "../../tables/hooks";
import type { TableShape } from "@web/library/relations";
import { useWorkSource, workKeys } from "../../work/hooks";
import { moreKey, useMoreSource } from "../hooks";
import {
    CATALOGUE,
    CATALOGUE_STALE,
    catalogueHits,
    chatCandidates,
    DEBOUNCE_MS,
    docHits,
    filesPerLemma,
    lemmaCandidates,
    MIN_SERVER_QUERY,
    pickRecordTables,
    recordHits,
    serverQueryOf,
    type CataloguePart,
    type Failure,
} from "./model";
import { groupByKind, rank, type Candidate, type Hit, type HitGroup } from "@web/search/matching";
import { recordSourcesFrom, type RecordSource } from "@web/search/sources";

/** Search's own reads, through react-query. */
export function useSearchSource() {
    return useMoreSource().search;
}

export interface SearchOutcome {
    hits: Hit[];
    /** The hits under one heading per kind, best group first (`groupByKind`). */
    groups: HitGroup[];
    /** The server's half is still being asked. What is held already shows. */
    searching: boolean;
    /** A Lemma's lists are still being read for the first time. */
    reading: boolean;
    /** What could not be reached, Lemma by Lemma. */
    failures: Failure[];
    /** Tables the record search left out to stay within its bound. */
    skippedTables: number;
    /** Ask again everything that failed. */
    retry: () => void;
}

/** Everything one box can find, in every Lemma or in one (`scope`).
 *
 *  Two halves, as on the web (`search/use-search.ts`), and the split is
 *  about cost. A Lemma's short lists (its agents, workflows, functions,
 *  apps, people, schedules, tables and recent chats) are read once, kept
 *  five minutes, and matched here on every keystroke. Files, older chats
 *  and rows are asked of the server once the words settle, and only from
 *  two letters on; rows cost a request per table, so they are bounded
 *  across the whole search and what was left out is said.
 *
 *  The lists another area already reads (agents, workflows, schedules, the
 *  roster, each table's columns) are read through that area under its key,
 *  so they are one request and one cache entry wherever they are shown.
 *  Each source fails on its own and is named when it does. */
export function useSearch(scope: string | null, query: string): SearchOutcome {
    const key = useCacheKey();
    const search = useSearchSource();
    const work = useWorkSource();
    const tables = useTablesSource();
    const chats = useChatsSource();
    const people = usePeopleSource();
    const all = useLemmas();
    const pods = useMemo(() => (scope ? all.lemmas.filter((one) => one.id === scope) : all.lemmas), [all.lemmas, scope]);
    const scoped = scope !== null;

    /* ── the cheap half ─────────────────────────────────────────── */
    const asked = useMemo(() => pods.flatMap((pod) => CATALOGUE.map((part) => ({ pod, part }))), [pods]);
    const catalogue = useQueries({
        queries: asked.map(({ pod, part }) => ({
            queryKey: catalogueKey(key, pod.id, part),
            queryFn: (): Promise<unknown> => {
                switch (part) {
                    case "chats": return chats.conversations(pod.id, null, null);
                    case "agents": return work.agents(pod.id);
                    case "workflows": return work.workflows(pod.id);
                    case "schedules": return work.schedules(pod.id);
                    case "people": return people.members(pod.id);
                    case "tables": return tables.tables(pod.id);
                    case "functions": return search.functions(pod.id);
                    case "apps": return search.apps(pod.id);
                }
            },
            staleTime: CATALOGUE_STALE,
            gcTime: 30 * 60_000,
            retry: 1,
        })),
    });
    const catalogueStamp = catalogue.map((one) => one.dataUpdatedAt).join(",");
    const local = useMemo(() => {
        const out: Candidate[] = scoped ? [] : lemmaCandidates(all.lemmas);
        asked.forEach(({ pod, part }, index) => {
            const data = catalogue[index]?.data;
            if (data !== undefined) out.push(...catalogueHits(pod, part, data));
        });
        return out;
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [asked, all.lemmas, scoped, catalogueStamp]);

    /* ── the expensive half ─────────────────────────────────────── */
    const [settled, setSettled] = useState(query.trim());
    useEffect(() => {
        const timer = setTimeout(() => setSettled(query.trim()), DEBOUNCE_MS);
        return () => clearTimeout(timer);
    }, [query]);
    const serverQuery = serverQueryOf(settled);
    const asking = serverQuery !== "";
    const limit = filesPerLemma(scoped);
    /* While new words are asked, the last answer stays on screen, ranked
       against what is typed now, rather than the list emptying between
       keystrokes. Only the same Lemma's: index i is another Lemma after the
       scope changes. */
    const held = (podId: string) => ({
        placeholderData: <T,>(previous: T | undefined, before?: { queryKey: readonly unknown[] }) => (before?.queryKey[3] === podId ? previous : undefined),
    });

    /* The chats held are the latest page; the server searches every title,
       so an old chat is found too. */
    const titled = useQueries({
        queries: pods.map((pod) => ({
            queryKey: moreKey(key, "search", pod.id, "chats", serverQuery),
            queryFn: () => chats.conversations(pod.id, null, serverQuery),
            enabled: asking,
            staleTime: 30_000,
            retry: false,
            ...held(pod.id),
        })),
    });
    const files = useQueries({
        queries: pods.map((pod) => ({
            queryKey: moreKey(key, "search", pod.id, "files", serverQuery, limit),
            queryFn: () => search.files(pod.id, serverQuery, limit),
            enabled: asking,
            staleTime: 30_000,
            retry: false,
            ...held(pod.id),
        })),
    });
    /* Which tables can hold words: a request per table, so asked only once
       something worth searching rows for has been typed. The tables area
       reads the same, under the same key, for what points at a row. */
    const shapes = useQueries({
        queries: pods.map((pod) => ({
            queryKey: tableKeys.shapes(key, pod.id),
            queryFn: () => tables.shapes(pod.id),
            enabled: asking,
            staleTime: CATALOGUE_STALE,
            retry: 1,
        })),
    });
    /* Picked once every Lemma's columns are in, so the bound is shared out
       over all of them rather than spent on whichever answered first. */
    const shapesIn = asking && shapes.every((one) => one.isSuccess || one.isError);
    const shapesStamp = shapes.map((one) => one.dataUpdatedAt).join(",");
    const bound = useMemo(() => {
        if (!shapesIn) return { picked: [] as { podId: string; source: RecordSource }[], skipped: 0 };
        return pickRecordTables(pods.map((pod, index) => ({
            podId: pod.id,
            sources: shapes[index]?.data ? recordSourcesFrom(shapes[index].data as TableShape[], Infinity).sources : [],
        })));
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [shapesIn, shapesStamp, pods]);
    const records = useQueries({
        queries: bound.picked.map(({ podId, source }) => ({
            queryKey: moreKey(key, "search", podId, "records", source.tableName, serverQuery),
            queryFn: () => search.records(podId, source, serverQuery),
            enabled: asking,
            staleTime: 30_000,
            retry: false,
        })),
    });

    const serverStamp = [titled, files, records].map((list) => list.map((one) => one.dataUpdatedAt).join(",")).join("|");
    const server = useMemo(() => {
        if (!asking) return [] as Candidate[];
        const out: Candidate[] = [];
        pods.forEach((pod, index) => {
            const found = titled[index]?.data as { items?: ConversationRef[] } | undefined;
            if (found?.items) out.push(...chatCandidates(pod.id, found.items));
            const docs = files[index]?.data;
            if (docs !== undefined) out.push(...docHits(pod.id, docs));
        });
        bound.picked.forEach(({ podId, source }, index) => {
            const rows = records[index]?.data;
            if (rows) out.push(...recordHits(podId, source, rows));
        });
        return out;
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [asking, pods, bound, serverStamp]);

    const hits = useMemo(() => {
        /* A recent chat is in both the page held and the server's answer: it
           is one hit, not two. */
        const seen = new Set(local.map((one) => one.kind + ":" + one.id));
        const fresh = server.filter((one) => !seen.has(one.kind + ":" + one.id));
        return rank([...local, ...fresh], query);
    }, [local, server, query]);
    const groups = useMemo(() => groupByKind(hits), [hits]);

    const failures: Failure[] = [];
    if (all.error) failures.push({ part: "lemmas", podId: null });
    asked.forEach(({ pod, part }, index) => {
        if (catalogue[index]?.isError) failures.push({ part, podId: pod.id });
    });
    if (asking) {
        pods.forEach((pod, index) => {
            if (titled[index]?.isError) failures.push({ part: "chats", podId: pod.id });
            if (files[index]?.isError) failures.push({ part: "files", podId: pod.id });
            /* Without a Lemma's columns there is no searching its rows, which
               is a failure to admit, not an empty answer. */
            if (shapes[index]?.isError) failures.push({ part: "records", podId: pod.id });
        });
        bound.picked.forEach(({ podId }, index) => {
            if (records[index]?.isError) failures.push({ part: "records", podId });
        });
    }

    const typed = query.trim();
    const fetching = [...titled, ...files, ...shapes, ...records].some((one) => one.isFetching);
    return {
        hits,
        groups,
        searching: typed.length >= MIN_SERVER_QUERY && (settled !== typed || (asking && fetching)),
        reading: all.pending || catalogue.some((one) => one.isPending),
        failures,
        skippedTables: bound.skipped,
        retry: () => {
            if (all.error) void all.refetch();
            for (const one of [...catalogue, ...titled, ...files, ...shapes, ...records] as UseQueryResult[]) {
                if (one.isError) void one.refetch();
            }
        },
    };
}

/** Each list under the key of the area that reads it, where one does, so a
 *  list on screen elsewhere is the same entry; search's own otherwise. */
function catalogueKey(key: string, podId: string, part: CataloguePart): readonly unknown[] {
    switch (part) {
        case "agents": return workKeys.agents(key, podId);
        case "workflows": return workKeys.workflows(key, podId);
        case "schedules": return workKeys.schedules(key, podId);
        /* People's own key in settings (`admin/people/hooks.ts`). */
        case "people": return adminKey(key, "people", podId, "members");
        default: return moreKey(key, "search", podId, part);
    }
}

/** A function's chat: the one bound to it, made the first time; null where
 *  none can be bound (sample data), and a new chat opens instead. */
export function useDiscuss() {
    const search = useSearchSource();
    return useMutation({
        mutationFn: ({ podId, name }: { podId: string; name: string; label: string }) => search.discuss(podId, "function", name),
    });
}
