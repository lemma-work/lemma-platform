import type { RecordSort } from "../types.js";
import type { DatastoreChangeFrame } from "../datastore-changes.js";

/**
 * Build a comparator from a record `sort` spec so a live list can keep its order
 * after deltas are merged in. Returns `undefined` when there's nothing to sort by
 * (the caller then leaves insertion order alone).
 */
export function makeRecordComparator<TRecord extends Record<string, unknown>>(
  sort?: RecordSort[],
): ((a: TRecord, b: TRecord) => number) | undefined {
  if (!sort || sort.length === 0) return undefined;
  return (a, b) => {
    for (const { field, direction } of sort) {
      const dir = direction === "desc" ? -1 : 1;
      const av = a[field];
      const bv = b[field];
      if (av == null && bv == null) continue;
      if (av == null) return -1 * dir;
      if (bv == null) return 1 * dir;
      // Compare structurally; values are typically strings/numbers/dates.
      if ((av as number | string) < (bv as number | string)) return -1 * dir;
      if ((av as number | string) > (bv as number | string)) return 1 * dir;
    }
    return 0;
  };
}

export interface ApplyDatastoreChangeOptions<TRecord extends Record<string, unknown>> {
  /** Primary-key field used to match rows (default `"id"`). */
  idKey?: string;
  /**
   * Optional predicate that decides whether a changed row belongs in this view.
   * Use it for filtered lists: a row that no longer matches is dropped. Omit it to
   * merge every change for the table.
   */
  accept?: (row: TRecord) => boolean;
  /** Optional comparator (see {@link makeRecordComparator}) re-applied after merge. */
  compare?: (a: TRecord, b: TRecord) => number;
}

/**
 * Pure reducer: apply one datastore change frame to a list of rows, returning a new
 * array (never mutating the input). This is the merge logic behind `useLiveRecords`,
 * factored out so it can be reasoned about and unit-tested without a WebSocket.
 *
 * - `delete` → drop the row by id.
 * - `insert` / `update` → upsert by id, shallow-merging the frame's `payload` over any
 *   existing row. The payload carries the whole row, so the merge is effectively a
 *   replace; it stays a merge so a client holding fields the server does not send
 *   (a local annotation, a joined label) keeps them.
 *   A row rejected by `accept` is removed (it left the filtered view).
 */
export function applyDatastoreChange<TRecord extends Record<string, unknown>>(
  rows: TRecord[],
  frame: DatastoreChangeFrame,
  options: ApplyDatastoreChangeOptions<TRecord> = {},
): TRecord[] {
  const idKey = options.idKey ?? "id";
  const id = frame.record_id;

  if (frame.operation === "delete") {
    return rows.filter((row) => row[idKey] !== id);
  }

  const existing = rows.find((row) => row[idKey] === id);
  const merged = { ...(existing ?? {}), ...frame.payload, [idKey]: id } as TRecord;

  if (options.accept && !options.accept(merged)) {
    // No longer matches this view — ensure it's absent.
    return existing ? rows.filter((row) => row[idKey] !== id) : rows;
  }

  let next: TRecord[];
  if (existing) {
    next = rows.map((row) => (row[idKey] === id ? merged : row));
  } else {
    next = [merged, ...rows];
  }

  return options.compare ? [...next].sort(options.compare) : next;
}
