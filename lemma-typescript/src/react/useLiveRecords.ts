import { useEffect, useMemo, useRef, useState } from "react";
import type { ChangeStreamStatus } from "../datastore-changes.js";
import { useRecords, type UseRecordsOptions, type UseRecordsResult } from "./useRecords.js";
import { useWatchChanges } from "./useWatchChanges.js";
import { applyDatastoreChange, makeRecordComparator } from "./datastoreChangeReducer.js";
import { stringifyComparable } from "./utils.js";

export interface UseLiveRecordsOptions<
  TRecord extends Record<string, unknown> = Record<string, unknown>,
> extends UseRecordsOptions {
  /** Subscribe to live changes (default `true`). When `false` this is just `useRecords`. */
  live?: boolean;
  /**
   * How to reconcile a change into the list:
   * - `"merge"` (default) — apply the delta in place (no refetch, no flicker).
   * - `"refetch"` — re-run the authoritative query (debounced) on each change. Use this
   *   for filtered/sorted views that must stay exact; it's event-driven, not polling.
   */
  reconcile?: "merge" | "refetch";
  /** Primary-key field used to match rows (default `"id"`). */
  idKey?: string;
  /**
   * Optional predicate keeping a changed row in a *filtered* view: the change stream
   * delivers every visible change for the table, so with `filters` an inserted row that
   * doesn't match could otherwise appear until the next refresh.
   */
  accept?: (row: TRecord) => boolean;
}

export interface UseLiveRecordsResult<
  TRecord extends Record<string, unknown> = Record<string, unknown>,
> extends UseRecordsResult<TRecord> {
  /** Current connection status of the underlying change stream. */
  liveStatus: ChangeStreamStatus;
}

/**
 * A record list that stays live without polling: an initial fetch (via `useRecords`,
 * with the same filters/sort/pagination) plus a subscription on the pod's shared live
 * socket that merges
 * insert/update/delete deltas **in place**, keyed by id. Rows update without a
 * full-list refetch, so the UI doesn't flicker and the API isn't hammered.
 *
 * ```tsx
 * const { records, isLoading, liveStatus } = useLiveRecords({ client, tableName: "tickets" });
 * return <ul>{records.map((r) => <li key={r.id as string}>{r.title as string}</li>)}</ul>;
 * ```
 *
 * Filtered views: the stream carries every change you can see, so pass `accept` (a
 * client-side predicate) or `reconcile: "refetch"` to keep a filtered list exact.
 */
export function useLiveRecords<TRecord extends Record<string, unknown> = Record<string, unknown>>(
  options: UseLiveRecordsOptions<TRecord>,
): UseLiveRecordsResult<TRecord> {
  const { live = true, reconcile = "merge", idKey = "id", accept, ...recordsOptions } = options;
  const base = useRecords<TRecord>(recordsOptions);

  // Local view of the rows: seeded from the base fetch, then kept live by the stream.
  const [rows, setRows] = useState<TRecord[]>(base.records);

  // Re-seed whenever the base list is replaced (initial load, refresh, loadMore,
  // filter/sort change). Live merges below touch only `rows`, never `base.records`,
  // so they are not clobbered between refetches.
  useEffect(() => {
    setRows(base.records);
  }, [base.records]);

  const sortKey = stringifyComparable(recordsOptions.sort);
  const compare = useMemo(
    () => makeRecordComparator<TRecord>(recordsOptions.sort),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [sortKey],
  );

  // Debounced refetch for reconcile:"refetch" — coalesces change bursts into one query.
  const refreshRef = useRef(base.refresh);
  refreshRef.current = base.refresh;
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    },
    [],
  );

  const scheduleRefresh = () => {
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      void refreshRef.current();
    }, 150);
  };

  const { status } = useWatchChanges({
    client: recordsOptions.client,
    podId: recordsOptions.podId,
    table: recordsOptions.tableName,
    enabled: live && (recordsOptions.enabled ?? true),
    // The server could not send what was missed (a bulk import, a long
    // disconnect): the authoritative query is the only way back in step.
    onResync: scheduleRefresh,
    onChange: (frame) => {
      if (reconcile === "refetch") {
        scheduleRefresh();
        return;
      }
      setRows((current) => applyDatastoreChange<TRecord>(current, frame, { idKey, accept, compare }));
    },
  });

  return useMemo(
    () => ({ ...base, records: rows, liveStatus: status }),
    [base, rows, status],
  );
}
