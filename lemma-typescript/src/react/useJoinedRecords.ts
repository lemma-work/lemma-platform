import { useCallback, useEffect, useMemo, useState } from "react";
import type { LemmaClient } from "../client.js";
import {
  buildJoinedRecordsQuery,
  parseForeignKeyReference,
  type JoinedRecordsQueryDefinition,
} from "../datastore-query.js";
import { normalizeError, resolvePodId, stringifyComparable } from "./utils.js";

/**
 * A simplified join descriptor. The hook auto-resolves the join condition
 * from the foreign-key metadata on the base table's column named by `on`.
 */
export interface JoinedRecordsShorthandJoin {
  /** The table to join into (e.g. "teams"). */
  table: string;
  /**
   * The FK column on the base table that references the join table
   * (e.g. "team_id"). The hook reads the FK metadata on this column
   * to determine the join condition automatically.
   */
  on: string;
  /** Optional alias for the joined table. Defaults to the table name. */
  alias?: string;
  /** Join type. Defaults to "left". */
  type?: "inner" | "left" | "left outer" | "right" | "right outer" | "full" | "full outer";
}

/**
 * React hook for cross-table join queries. Supports two API styles:
 *
 * 1. **Full query** via the `query` option — for complex joins with custom
 *    select lists, filters, and expressions.
 *
 * 2. **Shorthand** via `baseTable` + `joins` — the hook auto-resolves the
 *    join conditions from the schema's foreign-key metadata, so you don't
 *    need to spell out the `on` condition manually.
 *
 * @example Full query (existing API)
 * ```tsx
 * const { records, isLoading } = useJoinedRecords({
 *   client,
 *   query: {
 *     from: "issues",
 *     joins: [{ table: "teams", on: { left: "issues.team_id", right: "teams.id" } }],
 *   },
 * });
 * ```
 *
 * @example Shorthand with FK auto-resolution
 * ```tsx
 * const { records, isLoading } = useJoinedRecords({
 *   client,
 *   baseTable: "issues",
 *   joins: [{ table: "teams", on: "team_id" }],
 * });
 * // The hook reads the FK metadata on issues.team_id to find that
 * // it references teams.id, and builds the join automatically.
 * ```
 */
export interface UseJoinedRecordsOptions {
  client: LemmaClient;
  podId?: string;
  /** Full join query definition. Mutually exclusive with `baseTable` + `joins`. */
  query?: JoinedRecordsQueryDefinition;
  /** Base table for shorthand mode. Mutually exclusive with `query`. */
  baseTable?: string;
  /** Shorthand join descriptors. Auto-resolves join conditions from FK metadata. */
  joins?: JoinedRecordsShorthandJoin[];
  enabled?: boolean;
  autoLoad?: boolean;
}

export interface UseJoinedRecordsResult<TRecord extends Record<string, unknown> = Record<string, unknown>> {
  records: TRecord[];
  total: number;
  sql: string;
  isLoading: boolean;
  error: Error | null;
  refresh: () => Promise<TRecord[]>;
}

async function buildShorthandQuery(
  client: LemmaClient,
  podId: string | undefined,
  baseTableName: string,
  shorthandJoins: JoinedRecordsShorthandJoin[],
): Promise<JoinedRecordsQueryDefinition> {
  const resolvedPodId = resolvePodId(client, podId);
  const scopedClient = resolvedPodId === client.podId ? client : client.withPod(resolvedPodId);
  const baseTable = await scopedClient.tables.get(baseTableName.trim());

  const joins = await Promise.all(shorthandJoins.map(async (entry) => {
    const baseColumn = baseTable.columns.find((col) => col.name === entry.on) ?? null;
    const reference = baseColumn?.foreign_key?.references
      ? parseForeignKeyReference(baseColumn.foreign_key.references)
      : null;

    if (!baseColumn) {
      throw new Error(`Column "${entry.on}" was not found on table "${baseTableName}".`);
    }
    if (!reference) {
      throw new Error(`Column "${entry.on}" on "${baseTableName}" is not a foreign key. Use the full query API for non-FK joins.`);
    }

    return {
      type: entry.type ?? "left",
      table: entry.table,
      alias: entry.alias,
      on: {
        left: { table: baseTableName, column: entry.on },
        right: { table: entry.alias ?? entry.table, column: reference.column },
      },
    };
  }));

  return {
    from: { table: baseTableName, alias: baseTableName },
    joins,
  };
}

export function useJoinedRecords<TRecord extends Record<string, unknown> = Record<string, unknown>>({
  client,
  podId,
  query,
  baseTable,
  joins: shorthandJoins,
  enabled = true,
  autoLoad = true,
}: UseJoinedRecordsOptions): UseJoinedRecordsResult<TRecord> {
  const [records, setRecords] = useState<TRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [sql, setSql] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  const hasShorthand = !query && !!baseTable && !!shorthandJoins?.length;
  const queryKey = stringifyComparable(query);
  const shorthandKey = stringifyComparable({ baseTable, shorthandJoins });
  const stableQuery = useMemo(() => query, [queryKey]);
  // Same reason as `stableQuery` above: `joins` is an array, so a caller
  // writing it inline hands us a new reference every render. Left raw in
  // `refresh`'s dependencies it rebuilt the callback each time, which re-ran the
  // effect below and turned one mount into a query per render.
  const stableShorthandJoins = useMemo(() => shorthandJoins, [shorthandKey]);

  const refresh = useCallback(async (signal?: AbortSignal): Promise<TRecord[]> => {
    if (!enabled) {
      setRecords([]);
      setTotal(0);
      setSql("");
      setError(null);
      setIsLoading(false);
      return [];
    }

    setIsLoading(true);
    setError(null);

    try {
      const resolvedPodId = resolvePodId(client, podId);
      const scopedClient = resolvedPodId === client.podId ? client : client.withPod(resolvedPodId);

      let resolvedQuery = stableQuery;

      if (hasShorthand && baseTable && stableShorthandJoins) {
        resolvedQuery = await buildShorthandQuery(client, podId, baseTable, stableShorthandJoins);
      }

      if (!resolvedQuery) {
        setRecords([]);
        setTotal(0);
        setSql("");
        setIsLoading(false);
        return [];
      }

      if (signal?.aborted) return [];

      const nextSql = buildJoinedRecordsQuery(resolvedQuery);
      setSql(nextSql);

      const response = await scopedClient.datastore.query(nextSql);
      if (signal?.aborted) return [];
      const nextRecords = (response.items ?? []) as TRecord[];
      setRecords(nextRecords);
      setTotal(response.total ?? nextRecords.length);
      return nextRecords;
    } catch (refreshError) {
      if (signal?.aborted) return [];
      const normalized = normalizeError(refreshError, "Failed to load joined records.");
      setError(normalized);
      return [];
    } finally {
      if (!signal?.aborted) setIsLoading(false);
    }
  }, [client, enabled, hasShorthand, baseTable, podId, stableShorthandJoins, stableQuery]);

  useEffect(() => {
    if (!enabled) {
      setRecords([]);
      setTotal(0);
      setSql("");
      setError(null);
      setIsLoading(false);
      return;
    }

    if (!autoLoad) return;
    const controller = new AbortController();
    let cancelled = false;
    (async () => {
      try {
        await refresh(controller.signal);
      } catch {
        if (!cancelled) {
          setError(normalizeError(new Error("Failed to load joined records."), "Failed to load joined records."));
        }
      }
    })();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [autoLoad, enabled, refresh]);

  return useMemo(() => ({
    records,
    total,
    sql,
    isLoading,
    error,
    refresh,
  }), [error, isLoading, records, refresh, sql, total]);
}
