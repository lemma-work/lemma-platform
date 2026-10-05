"use client";

import { useEffect, useMemo, useState } from "react";
import { useInfiniteQuery, useQueries, useQuery } from "@tanstack/react-query";
import { lemma } from "@/session/client";
import { source, type ConversationRef, type LibraryItem, type Pod } from "@/data";
import type { AgentRow } from "@/data/agents";
import type { StandingJob } from "@/schedule/schedules";
import { rank, type Candidate, type Hit } from "./matching";
import {
    agentCandidates,
    appCandidates,
    functionCandidates,
    itemsOf,
    nameOf,
    personCandidates,
    recordSearchSql,
    recordSourcesFrom,
    scheduleCandidates,
    workflowCandidates,
} from "./sources";

/** Everything one box can find.
 *
 *  Two kinds of source, and the split is about cost rather than taste.
 *
 *  Most of what lives in a pod is a short list — a handful of agents, a few
 *  workflows, the people who are in it. Those are fetched once, cached, and
 *  matched in the browser, which is why typing feels immediate and why a
 *  second keystroke costs nothing.
 *
 *  Documents and records are not short lists. Files are searched on the server,
 *  where the index is; records are searched per table, which is the one source
 *  that costs a request *per table* and so is bounded and debounced.
 */

const CATALOGUE_STALE = 5 * 60_000;

/** Long enough that a phrase is typed rather than transmitted a letter at a
 *  time, short enough not to feel like waiting. Only the server-backed sources
 *  use it; local matching runs on every keystroke. */
const DEBOUNCE_MS = 180;

/** Below this, a server search is all noise — and an expensive way to get it. */
const MIN_SERVER_QUERY = 2;

/* Readers for lists other screens already hold, put back into the shape the
   raw-response readers expect. Module-level so `select` keeps one reference
   and does not re-derive the candidates on every render. */
function agentHits(rows: AgentRow[]): Candidate[] {
    return agentCandidates(rows.map((row) => ({ id: row.id, name: row.name, description: row.blurb })));
}

function scheduleHits(jobs: StandingJob[]): Candidate[] {
    return scheduleCandidates(jobs.map((job) => ({ id: job.id, name: job.name, cron: job.triggerLiteral, agent_name: job.target.name })));
}

function tableHits(data: { pages: { items: LibraryItem[] }[] }): Candidate[] {
    return data.pages.flatMap((page) => page.items).map((item): Candidate => ({ kind: "table", id: item.id, title: item.name, subtitle: item.detail, payload: item }));
}

export interface SearchOutcome {
    hits: Hit[];
    /** The server-backed halves are still in flight. Local hits already show. */
    isSearching: boolean;
    /** Sources that failed, by name, so the list can admit it is partial
     *  rather than quietly returning less. */
    failed: string[];
    /** Tables a record search could not reach within its bound. */
    skippedTables: number;
}

export function useSearch(podId: string | null, pods: Pod[], query: string): SearchOutcome {
    const client = useMemo(() => (podId ? lemma(podId) : null), [podId]);
    const enabled = Boolean(podId && client);

    /* ── the cheap half: one list each, cached, matched locally ────── */
    const own = useQueries({
        queries: [
            { key: "functions", run: () => client!.functions.list({ limit: 100 }), read: functionCandidates },
            { key: "workflows", run: () => client!.workflows.list({ limit: 100 }), read: workflowCandidates },
            { key: "apps", run: () => client!.apps.list({ limit: 100 }), read: appCandidates },
            { key: "people", run: () => client!.podMembers.list(podId!, { limit: 100 }), read: personCandidates },
        ].map(({ key, run, read }) => ({
            queryKey: ["search", podId, key],
            queryFn: async () => read(await run()),
            enabled,
            staleTime: CATALOGUE_STALE,
            gcTime: 30 * 60_000,
        })),
    });

    /* Agents, schedules and tables are lists the pod's own screens already
       read, so they come from those caches — the same key and the same
       request — rather than a second copy of each under `["search", …]`. */
    const agents = useQuery({
        queryKey: ["agents", podId],
        queryFn: () => source.listAgents(podId as string),
        enabled,
        staleTime: 60_000,
        select: agentHits,
    });
    const schedules = useQuery({
        queryKey: ["schedules", podId],
        queryFn: () => source.listSchedules(podId as string),
        enabled,
        staleTime: 30_000,
        select: scheduleHits,
    });
    /* The library's first page of tables: fifty, the same fifty the Tables
       filter shows first. A pod with more than that is rare. */
    const tables = useInfiniteQuery({
        queryKey: ["library", podId, "tables"],
        initialPageParam: undefined as string | undefined,
        queryFn: ({ pageParam }) => source.listLibrary(podId as string, "tables", "/", pageParam),
        getNextPageParam: (page) => page.next || undefined,
        enabled,
        staleTime: 60_000,
        select: tableHits,
    });
    const catalogue = [agents, ...own, schedules, tables];
    const catalogueNames = ["agents", "functions", "workflows", "apps", "people", "schedules", "tables"];

    /* Conversations and teammates are already on screen elsewhere, so they are
       read from the caches that drew them rather than fetched again. */
    const conversations = useQuery({
        queryKey: ["conversations", podId],
        queryFn: () => source.listConversations(podId as string),
        enabled,
        staleTime: 60_000,
    });

    const local = useMemo<Candidate[]>(() => {
        const out: Candidate[] = [];
        /* Teammates are the one source that is not pod-scoped: you search for
           somebody to talk to far more often than you search inside the pod
           you happen to be standing in. */
        for (const pod of pods) {
            out.push({ kind: "teammate", id: pod.id, title: pod.name, subtitle: pod.subtitle || null, payload: pod });
        }
        for (const entry of (conversations.data ?? []) as ConversationRef[]) {
            out.push({ kind: "conversation", id: entry.id, title: entry.title, subtitle: entry.at, payload: entry });
        }
        for (const result of catalogue) {
            if (result.data) out.push(...result.data);
        }
        return out;
    }, [pods, conversations.data, catalogue.map(r => r.dataUpdatedAt).join(",")]);

    /* ── the expensive half: asked of the server, and only when asked ── */
    const [settled, setSettled] = useState("");
    useEffect(() => {
        const timer = window.setTimeout(() => setSettled(query.trim()), DEBOUNCE_MS);
        return () => window.clearTimeout(timer);
    }, [query]);

    const serverQuery = settled.length >= MIN_SERVER_QUERY ? settled : "";

    /* The cached list above is one page — the most recent conversations. The
       server searches every title, so an old conversation is findable too. */
    const olderConversations = useQuery({
        queryKey: ["search", podId, "conversations", serverQuery],
        queryFn: async () => {
            const found = await source.listConversationsPage(podId as string, null, serverQuery);
            return found.items.map((entry): Candidate => ({
                kind: "conversation", id: entry.id, title: entry.title, subtitle: entry.at, payload: entry,
            }));
        },
        enabled: enabled && Boolean(serverQuery),
        staleTime: 30_000,
    });

    const docs = useQuery({
        queryKey: ["search", podId, "docs", serverQuery],
        queryFn: async () => {
            const found = await client!.files.search(serverQuery, { limit: 12, searchMethod: "HYBRID" });
            return itemsOf(found).map((item, index): Candidate => {
                const path = String(item.path ?? item.file_path ?? "");
                return {
                    kind: "doc",
                    id: path || "doc-" + index,
                    title: nameOf(item, path.split("/").filter(Boolean).pop() || "Document"),
                    subtitle: path || null,
                    /* The path matters as much as the name for a document —
                       people remember where a thing lives at least as often as
                       what it is called. */
                    haystack: path,
                    payload: item,
                };
            });
        },
        enabled: enabled && Boolean(serverQuery),
        staleTime: 30_000,
    });

    /* Which tables can hold text. `tables.list` deliberately omits columns,
       so this is a request per table — which is why it waits until something
       worth searching records for has been typed, rather than firing as the
       palette opens. Every table's shape is what the record page reads too,
       under the same key; an unreadable table comes back without columns
       there, which RLS can do, and is simply not searched. */
    const columns = useQuery({
        queryKey: ["table-shapes", podId],
        queryFn: () => source.tableShapes(podId as string) as Promise<{ name: string; columns?: { name: string; type?: string }[] }[]>,
        enabled: enabled && Boolean(serverQuery),
        staleTime: CATALOGUE_STALE,
        select: recordSourcesFrom,
    });

    const recordSources = columns.data?.sources ?? [];
    /* One SELECT per table rather than one per column. `records.query` ANDs its
       filters and offers no OR, so matching any of a table's text columns
       through it would be a request each — where a single statement does the
       whole table. The query is escaped in `sqlLiteral`, and the endpoint only
       permits one read-only SELECT under RLS. */
    const records = useQuery({
        queryKey: ["search", podId, "records", serverQuery, recordSources.map(s => s.tableName).join(",")],
        queryFn: async () => {
            const scoped = lemma(podId as string);
            const found = await Promise.all(
                recordSources.map(async (table) => {
                    try {
                        const answer = await scoped.datastore.query(recordSearchSql(table, serverQuery));
                        return itemsOf(answer).map((row, index): Candidate => ({
                            kind: "record",
                            id: table.tableName + ":" + String(row.id ?? index),
                            title: String(row[table.displayField ?? ""] ?? row.id ?? "Row"),
                            subtitle: table.label,
                            payload: { table: table.tableName, row },
                        }));
                    } catch {
                        /* One table that cannot be read — RLS, a type that will
                           not cast — is not a reason to have no record results
                           at all. */
                        return [] as Candidate[];
                    }
                }),
            );
            return found.flat();
        },
        enabled: enabled && Boolean(serverQuery) && recordSources.length > 0,
        staleTime: 30_000,
    });

    const hits = useMemo(() => {
        /* A recent conversation is in both the cached page and the server's
           answer; it is one result, not two. */
        const seen = new Set(local.filter((c) => c.kind === "conversation").map((c) => c.id));
        const older = (olderConversations.data ?? []).filter((c) => !seen.has(c.id));
        return rank([...local, ...older, ...(docs.data ?? []), ...(records.data ?? [])], query);
    }, [local, olderConversations.data, docs.data, records.data, query]);

    const failed = catalogueNames.filter((_, index) => catalogue[index].isError);
    if (olderConversations.isError) failed.push("conversations");
    if (docs.isError) failed.push("documents");
    if (records.isError) failed.push("records");

    return {
        hits,
        isSearching: Boolean(serverQuery) && (olderConversations.isFetching || docs.isFetching || columns.isFetching || records.isFetching || settled !== query.trim()),
        failed,
        skippedTables: columns.data?.skipped ?? 0,
    };
}
