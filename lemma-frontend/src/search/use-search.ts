"use client";

import { useEffect, useMemo, useState } from "react";
import { useQueries, useQuery } from "@tanstack/react-query";
import { lemma } from "@/session/client";
import { source, type ConversationRef, type Pod } from "@/data";
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
    tableCandidates,
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
    const catalogue = useQueries({
        queries: [
            { key: "agents", run: () => client!.agents.list({ limit: 100 }), read: agentCandidates },
            { key: "functions", run: () => client!.functions.list({ limit: 100 }), read: functionCandidates },
            { key: "workflows", run: () => client!.workflows.list({ limit: 100 }), read: workflowCandidates },
            { key: "apps", run: () => client!.apps.list({ limit: 100 }), read: appCandidates },
            { key: "schedules", run: () => client!.schedules.list({ limit: 100 }), read: scheduleCandidates },
            { key: "tables", run: () => client!.tables.list({ limit: 100 }), read: tableCandidates },
            { key: "people", run: () => client!.podMembers.list(podId!, { limit: 100 }), read: personCandidates },
        ].map(({ key, run, read }) => ({
            queryKey: ["search", podId, key],
            queryFn: async () => read(await run()),
            enabled,
            staleTime: CATALOGUE_STALE,
            gcTime: 30 * 60_000,
        })),
    });
    const catalogueNames = ["agents", "functions", "workflows", "apps", "schedules", "tables", "people"];

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

    /* Which tables can hold text, resolved once. `tables.list` deliberately
       omits columns, so this is a request per table — done on the catalogue's
       schedule rather than on the query's. */
    const tableNames = useMemo(
        () => (catalogue[5].data ?? []).map((candidate) => candidate.title),
        [catalogue[5].data],
    );
    const columns = useQuery({
        queryKey: ["search", podId, "columns", tableNames.join(",")],
        queryFn: async () => {
            const loaded = await Promise.all(
                tableNames.map(async (name) => {
                    try {
                        const detail = await client!.tables.get(name);
                        return { name, columns: (detail as { columns?: { name: string; type?: string }[] }).columns ?? [] };
                    } catch {
                        /* One unreadable table is not a reason to have no record
                           search — RLS can hide a table from the person asking. */
                        return { name, columns: [] };
                    }
                }),
            );
            return recordSourcesFrom(loaded);
        },
        enabled: enabled && tableNames.length > 0,
        staleTime: CATALOGUE_STALE,
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

    const hits = useMemo(
        () => rank([...local, ...(docs.data ?? []), ...(records.data ?? [])], query),
        [local, docs.data, records.data, query],
    );

    const failed = catalogueNames.filter((_, index) => catalogue[index].isError);
    if (docs.isError) failed.push("documents");
    if (records.isError) failed.push("records");

    return {
        hits,
        isSearching: Boolean(serverQuery) && (docs.isFetching || records.isFetching || settled !== query.trim()),
        failed,
        skippedTables: columns.data?.skipped ?? 0,
    };
}
