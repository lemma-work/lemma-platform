import { useCallback, useEffect, useState } from "react";
import { useInfiniteQuery, useMutation, useQueries, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useCacheKey } from "../../data/hooks";
import { pollWhenDown, useLiveStatus, useLiveTopic } from "../../live/pod-live";
import { useArea } from "../use-area";
import { applyChanges, withMade, type RecordChange } from "./layouts";
import type { Row } from "./source";
import type { WebWidget } from "@web/data/contacts";
import type { Audience } from "@web/data/public-rows";
import { idOf, withUpdatedRow, withoutRow, type RowPages } from "@web/library/record-cache";
import { LINKED_ROWS_SHOWN, type InwardLink, type OutwardLink } from "@web/library/relations";

/** The tables screens' reads and writes, through react-query. Every key
 *  starts with `useCacheKey()`, so sample data and each server keep their own
 *  cache entries. */
export function useTablesSource() {
    return useArea("tables");
}

/** The keys, written once, so a write patches exactly what a screen reads. */
export const tableKeys = {
    list: (key: string, podId: string) => [key, "tables", podId, "list"] as const,
    shape: (key: string, podId: string, name: string) => [key, "tables", podId, name, "shape"] as const,
    shapes: (key: string, podId: string) => [key, "tables", podId, "shapes"] as const,
    rows: (key: string, podId: string, name: string) => [key, "tables", podId, name, "rows"] as const,
    count: (key: string, podId: string, name: string) => [key, "tables", podId, name, "count"] as const,
    record: (key: string, podId: string, name: string, id: string) => [key, "tables", podId, name, "record", id] as const,
    referencing: (key: string, podId: string, name: string) => [key, "tables", podId, name, "referencing"] as const,
    opening: (key: string, podId: string, name: string) => [key, "tables", podId, name, "opening"] as const,
    chats: (key: string, podId: string) => [key, "tables", podId, "web-chats"] as const,
};

export function useTableList(podId: string) {
    const source = useTablesSource();
    const key = useCacheKey();
    return useInfiniteQuery({
        queryKey: tableKeys.list(key, podId),
        initialPageParam: undefined as string | undefined,
        queryFn: ({ pageParam }) => source.tables(podId, pageParam),
        getNextPageParam: (page) => page.next ?? undefined,
        staleTime: 60_000,
    });
}

export function useShape(podId: string, name: string) {
    const source = useTablesSource();
    const key = useCacheKey();
    return useQuery({ queryKey: tableKeys.shape(key, podId, name), queryFn: () => source.shape(podId, name), staleTime: 5 * 60_000 });
}

/** Every table's shape: what points at a row is declared on the others. */
export function useShapes(podId: string) {
    const source = useTablesSource();
    const key = useCacheKey();
    return useQuery({ queryKey: tableKeys.shapes(key, podId), queryFn: () => source.shapes(podId), staleTime: 5 * 60_000 });
}

/** The table's rows a page at a time, its count beside them, and the rest
 *  fetched straight away when the table is small enough to read whole, so a
 *  layout is chosen from all of it. Kept live by the pod's socket; read again
 *  once a minute only while the socket is down. */
export function useRows(podId: string, name: string, primaryKey: string, whole: number) {
    const source = useTablesSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    const status = useLiveStatus(podId);
    const rows = useInfiniteQuery({
        queryKey: tableKeys.rows(key, podId, name),
        initialPageParam: undefined as string | undefined,
        queryFn: ({ pageParam }) => source.rows(podId, name, pageParam),
        getNextPageParam: (page) => page.next ?? undefined,
        staleTime: 60_000,
        refetchInterval: pollWhenDown(status, 60_000),
    });
    const count = useQuery({
        queryKey: tableKeys.count(key, podId, name),
        queryFn: () => source.count(podId, name),
        staleTime: 60_000,
        refetchInterval: pollWhenDown(status, 60_000),
    });
    const known = count.isFetched ? count.data : undefined;
    const readWhole = known !== null && known !== undefined && known <= whole;
    useEffect(() => {
        if (readWhole && rows.hasNextPage && !rows.isFetchingNextPage && !rows.isError) void rows.fetchNextPage();
    }, [readWhole, rows.hasNextPage, rows.isFetchingNextPage, rows.isError, rows]);

    useLiveTopic(podId, "records", { table: name }, {
        onEvents: (events) => {
            const changes = (events as RecordChange[]).filter((change) => change && change.table === name);
            if (changes.length === 0) return;
            let moved = 0;
            cache.setQueryData<RowPages>(tableKeys.rows(key, podId, name), (pages) => {
                const applied = applyChanges(pages, primaryKey, changes);
                moved = applied.moved;
                return applied.pages;
            });
            if (moved !== 0) cache.setQueryData<number | null>(tableKeys.count(key, podId, name), (was) => (typeof was === "number" ? Math.max(0, was + moved) : was));
            for (const change of changes) {
                const record = tableKeys.record(key, podId, name, change.record_id);
                if (change.op === "delete") cache.removeQueries({ queryKey: record, exact: true });
                else if (change.row) cache.setQueryData<Row>(record, (was) => (was ? { ...was, ...change.row } : was));
            }
        },
        onResync: () => {
            void cache.invalidateQueries({ queryKey: tableKeys.rows(key, podId, name) });
            void cache.invalidateQueries({ queryKey: tableKeys.count(key, podId, name) });
        },
    });

    const refresh = useCallback(async () => {
        await Promise.all([rows.refetch(), count.refetch()]);
    }, [rows, count]);
    return { rows, count, readWhole, refresh, live: status === "open" };
}

/** One row on its own page, kept live by its table's subscription. An empty
 *  id is a row not made yet: nothing to read. */
export function useRecord(podId: string, name: string, id: string) {
    const source = useTablesSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    const record = useQuery({ queryKey: tableKeys.record(key, podId, name, id), queryFn: () => source.record(podId, name, id), enabled: Boolean(id), staleTime: 30_000, retry: false });
    useLiveTopic(id ? podId : null, "records", { table: name }, {
        onEvents: (events) => {
            for (const change of events as RecordChange[]) {
                if (!change || change.table !== name || change.record_id !== id) continue;
                if (change.op === "delete") void record.refetch();
                else if (change.row) cache.setQueryData<Row>(tableKeys.record(key, podId, name, id), (was) => ({ ...(was ?? {}), ...change.row }));
            }
        },
        onResync: () => void record.refetch(),
    });
    return record;
}

/** A page of a table's rows, so a row's own page can tell a status from free
 *  text the way the table does: one row cannot say, fifty can. The table's
 *  own rows stand in while it is read, when they are already here. */
export function useRowSample(podId: string, name: string) {
    const source = useTablesSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    return useQuery({
        queryKey: [...tableKeys.rows(key, podId, name), "sample"],
        queryFn: async () => (await source.rows(podId, name)).items,
        placeholderData: () => cache.getQueryData<RowPages>(tableKeys.rows(key, podId, name))?.pages.flatMap((page) => page.items),
        staleTime: 5 * 60_000,
        retry: false,
    });
}

/** The rows on the far end of each link out of a row. */
export function useLinkedOut(podId: string, links: OutwardLink[], row: Row | undefined) {
    const source = useTablesSource();
    const key = useCacheKey();
    return useQueries({
        queries: links.map((link) => {
            const value = row?.[link.column];
            const id = value === null || value === undefined ? "" : String(value);
            return {
                queryKey: tableKeys.record(key, podId, link.table, id),
                queryFn: () => source.record(podId, link.table, id),
                enabled: Boolean(id),
                staleTime: 60_000,
                retry: false,
            };
        }),
    });
}

/** The rows of other tables that point back at this one, a few per link. */
export function useLinkedBack(podId: string, links: InwardLink[], id: string) {
    const source = useTablesSource();
    const key = useCacheKey();
    return useQueries({
        queries: links.map((link) => ({
            queryKey: [...tableKeys.referencing(key, podId, link.table), link.column, id],
            queryFn: () => source.referencing(podId, link.table, link.column, id, LINKED_ROWS_SHOWN + 1),
            enabled: Boolean(id),
            staleTime: 60_000,
            retry: false,
        })),
    });
}

/** After a row is written: the table's pages, its count and the row's own
 *  page all say so at once, rather than on the next read. */
export function patchSaved(cache: QueryClient, key: string, podId: string, name: string, primaryKey: string, saved: Row, mode: "create" | "update", id?: string) {
    const rows = tableKeys.rows(key, podId, name);
    if (mode === "create") {
        const { pages, added } = withMade(cache.getQueryData<RowPages>(rows), primaryKey, saved);
        cache.setQueryData<RowPages>(rows, pages);
        if (added) cache.setQueryData<number | null>(tableKeys.count(key, podId, name), (was) => (typeof was === "number" ? was + 1 : was));
    } else if (id) {
        cache.setQueryData<RowPages>(rows, (pages) => withUpdatedRow(pages, primaryKey, id, saved));
    }
    const savedId = idOf(saved, primaryKey) ?? id;
    if (savedId) cache.setQueryData<Row>(tableKeys.record(key, podId, name, savedId), (was) => ({ ...(was ?? {}), ...saved }));
    void cache.invalidateQueries({ queryKey: [key, "tables", podId], predicate: (query) => query.queryKey.includes("referencing") });
}

/** One field changed where it is, or a row deleted: the screen changes first,
 *  the Lemma is told, and if it disagrees the screen goes back and says so. */
export function useRowWrites(podId: string, name: string, primaryKey: string) {
    const source = useTablesSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    const [problem, setProblem] = useState<string | null>(null);
    const rowsKey = tableKeys.rows(key, podId, name);

    const change = useCallback(async (row: Row, changes: Row) => {
        const id = idOf(row, primaryKey);
        if (!id) { setProblem(`This row has no ${primaryKey}, so there is no way to save a change to it.`); return; }
        const before = cache.getQueryData<RowPages>(rowsKey);
        const record = tableKeys.record(key, podId, name, id);
        const recordBefore = cache.getQueryData<Row>(record);
        setProblem(null);
        cache.setQueryData<RowPages>(rowsKey, (pages) => withUpdatedRow(pages, primaryKey, id, changes));
        if (recordBefore) cache.setQueryData<Row>(record, { ...recordBefore, ...changes });
        try {
            const saved = await source.update(podId, name, id, changes);
            cache.setQueryData<RowPages>(rowsKey, (pages) => withUpdatedRow(pages, primaryKey, id, saved));
        } catch (failure) {
            cache.setQueryData(rowsKey, before);
            if (recordBefore) cache.setQueryData(record, recordBefore);
            setProblem(failure instanceof Error && failure.message ? failure.message : "That change was not saved. It has been put back.");
        }
    }, [cache, key, name, podId, primaryKey, rowsKey, source]);

    const remove = useCallback(async (row: Row): Promise<boolean> => {
        const id = idOf(row, primaryKey);
        if (!id) { setProblem(`This row has no ${primaryKey}, so there is no way to delete it from here.`); return false; }
        const before = cache.getQueryData<RowPages>(rowsKey);
        const countKey = tableKeys.count(key, podId, name);
        const countBefore = cache.getQueryData<number | null>(countKey);
        setProblem(null);
        cache.setQueryData<RowPages>(rowsKey, (pages) => withoutRow(pages, primaryKey, id));
        if (typeof countBefore === "number") cache.setQueryData(countKey, Math.max(0, countBefore - 1));
        try {
            await source.remove(podId, name, id);
            /* Later, not now: the row's own page may still be on screen,
               leaving, and would read the row again only to say it is gone. */
            setTimeout(() => cache.removeQueries({ queryKey: tableKeys.record(key, podId, name, id), exact: true }), 1500);
            return true;
        } catch (failure) {
            cache.setQueryData(rowsKey, before);
            cache.setQueryData(countKey, countBefore);
            setProblem(failure instanceof Error && failure.message ? failure.message : "That row was not deleted. It has been put back.");
            return false;
        }
    }, [cache, key, name, podId, primaryKey, rowsKey, source]);

    return { change, remove, problem, clearProblem: () => setProblem(null) };
}

/* ---- collecting responses ---------------------------------------------- */

export function useOpening(podId: string, name: string) {
    const source = useTablesSource();
    const key = useCacheKey();
    return useQuery({ queryKey: tableKeys.opening(key, podId, name), queryFn: () => source.opening(podId, name) });
}

export function useWebChats(podId: string) {
    const source = useTablesSource();
    const key = useCacheKey();
    return useQuery({ queryKey: tableKeys.chats(key, podId), queryFn: () => source.webChats(podId) });
}

export function useSaveOpening(podId: string, name: string) {
    const source = useTablesSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    return useMutation({
        mutationFn: async ({ to, columns }: { to: Audience | "off"; columns: string[] }) => {
            if (to === "off") {
                await source.closeTable(podId, name);
                return null;
            }
            return source.openTable(podId, name, to, columns);
        },
        onSuccess: () => void cache.invalidateQueries({ queryKey: tableKeys.opening(key, podId, name) }),
    });
}

export function useMakeWebChat(podId: string) {
    const source = useTablesSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    return useMutation({
        mutationFn: ({ name, answer }: { name: string; answer: WebWidget["answer"] }) => source.makeWebChat(podId, name, answer),
        onSuccess: () => void cache.invalidateQueries({ queryKey: tableKeys.chats(key, podId) }),
    });
}
