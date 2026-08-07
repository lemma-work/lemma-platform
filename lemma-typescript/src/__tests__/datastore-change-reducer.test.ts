import { describe, expect, it } from "vitest";
import {
  applyDatastoreChange,
  makeRecordComparator,
} from "../react/datastoreChangeReducer.js";
import type { DatastoreChangeFrame } from "../datastore-changes.js";

type Row = Record<string, unknown>;

function frame(partial: Partial<DatastoreChangeFrame> & Pick<DatastoreChangeFrame, "operation" | "record_id">): DatastoreChangeFrame {
  return {
    type: `datastore.record.${partial.operation}`,
    pod_id: "pod_1",
    table_name: "tickets",
    payload: {},
    ...partial,
  };
}

describe("applyDatastoreChange", () => {
  const rows: Row[] = [
    { id: "a", title: "Alpha", status: "open" },
    { id: "b", title: "Bravo", status: "open" },
  ];

  it("inserts a new row at the front and does not mutate the input", () => {
    const next = applyDatastoreChange(rows, frame({ operation: "insert", record_id: "c", payload: { title: "Charlie", status: "open" } }));
    expect(next).toHaveLength(3);
    expect(next[0]).toEqual({ id: "c", title: "Charlie", status: "open" });
    expect(rows).toHaveLength(2); // unchanged
  });

  it("updates in place, keeping fields the frame does not carry", () => {
    // The server sends the whole row, so this merge is usually a replace. It
    // stays a merge so a client holding its own fields — a local annotation, a
    // joined label — does not lose them to an update.
    const next = applyDatastoreChange(rows, frame({ operation: "update", record_id: "a", payload: { status: "done" } }));
    expect(next).toHaveLength(2);
    expect(next.find((r) => r.id === "a")).toEqual({ id: "a", title: "Alpha", status: "done" });
  });

  it("takes every column of a whole-row update, including ones it had not seen", () => {
    const next = applyDatastoreChange(
      rows,
      frame({ operation: "update", record_id: "a", payload: { id: "a", title: "Alpha", status: "done", assignee: "kim" } }),
    );
    expect(next.find((r) => r.id === "a")).toEqual({ id: "a", title: "Alpha", status: "done", assignee: "kim" });
  });

  it("deletes by id", () => {
    const next = applyDatastoreChange(rows, frame({ operation: "delete", record_id: "b" }));
    expect(next.map((r) => r.id)).toEqual(["a"]);
  });

  it("upserts an update for an unseen id (insert semantics)", () => {
    const next = applyDatastoreChange(rows, frame({ operation: "update", record_id: "z", payload: { title: "Zed" } }));
    expect(next.find((r) => r.id === "z")).toEqual({ id: "z", title: "Zed" });
  });

  it("drops a row that no longer matches the accept predicate", () => {
    const accept = (r: Row) => r.status === "open";
    const next = applyDatastoreChange(rows, frame({ operation: "update", record_id: "a", payload: { status: "done" } }), { accept });
    expect(next.map((r) => r.id)).toEqual(["b"]);
  });

  it("ignores an insert rejected by the accept predicate", () => {
    const accept = (r: Row) => r.status === "open";
    const next = applyDatastoreChange(rows, frame({ operation: "insert", record_id: "c", payload: { status: "closed" } }), { accept });
    expect(next).toHaveLength(2);
  });

  it("re-applies the comparator after a merge", () => {
    const compare = makeRecordComparator<Row>([{ field: "title", direction: "asc" }]);
    const next = applyDatastoreChange(rows, frame({ operation: "insert", record_id: "c", payload: { title: "Aardvark" } }), { compare });
    expect(next.map((r) => r.title)).toEqual(["Aardvark", "Alpha", "Bravo"]);
  });

  it("respects a custom idKey", () => {
    const custom: Row[] = [{ sku: "x", qty: 1 }];
    const next = applyDatastoreChange(custom, frame({ operation: "update", record_id: "x", payload: { qty: 5 } }), { idKey: "sku" });
    expect(next).toEqual([{ sku: "x", qty: 5 }]);
  });
});

describe("makeRecordComparator", () => {
  it("returns undefined when there is nothing to sort by", () => {
    expect(makeRecordComparator(undefined)).toBeUndefined();
    expect(makeRecordComparator([])).toBeUndefined();
  });

  it("sorts descending and pushes nullish values last by direction", () => {
    const compare = makeRecordComparator<Row>([{ field: "n", direction: "desc" }])!;
    const sorted = [{ n: 1 }, { n: 3 }, { n: 2 }].sort(compare);
    expect(sorted.map((r) => r.n)).toEqual([3, 2, 1]);
  });
});
