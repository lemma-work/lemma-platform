import { act, createElement, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { LemmaClient } from "../client.js";
import { useJoinedRecords, type JoinedRecordsShorthandJoin } from "../react/index.js";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const roots: Root[] = [];

function fakeClient() {
  const query = vi.fn(async () => ({ items: [{ id: "r1" }], total: 1 }));
  const tablesGet = vi.fn(async () => ({
    name: "members",
    columns: [
      { name: "id", foreign_key: null },
      { name: "team_id", foreign_key: { references: "teams.id" } },
    ],
  }));

  const client = {
    podId: "pod-1",
    withPod() {
      return this;
    },
    tables: { get: tablesGet },
    datastore: { query },
  } as unknown as LemmaClient;

  return { client, query, tablesGet };
}

async function settle() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

afterEach(async () => {
  while (roots.length > 0) {
    const root = roots.pop();
    if (!root) continue;
    await act(async () => root.unmount());
  }
  document.body.innerHTML = "";
});

/**
 * Mount the hook with `joins` built fresh on every render, the way a caller
 * writing the array inline in JSX does. `rerender` bumps unrelated state, so
 * nothing about the query changes -- only the array's identity.
 */
async function mountWithInlineJoins(client: LemmaClient) {
  const rerender = { current: null as (() => void) | null };
  let renders = 0;

  function Harness() {
    const [tick, setTick] = useState(0);
    rerender.current = () => setTick((value) => value + 1);
    renders += 1;
    // Without the fix this component never stops: each render hands the hook a
    // new array, which rebuilds `refresh`, which re-runs the load effect, which
    // sets state and renders again. Throwing turns that into a failure that
    // says so, instead of the suite hanging until the 5s timeout.
    if (renders > 25) {
      throw new Error(
        `useJoinedRecords render loop: ${renders} renders from one mount with an inline joins array`,
      );
    }
    void tick;
    // A new array literal every render, which is the whole point.
    const joins: JoinedRecordsShorthandJoin[] = [{ table: "teams", on: "team_id" }];
    useJoinedRecords({ client, podId: "pod-1", baseTable: "members", joins });
    return null;
  }

  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  roots.push(root);
  await act(async () => {
    root.render(createElement(Harness));
    await Promise.resolve();
  });
  await settle();
  return { rerender, renderCount: () => renders };
}

describe("useJoinedRecords with an inline joins array", () => {
  it("does not re-query when a re-render passes a fresh but equal joins array", async () => {
    // `joins` is an array, so an inline one is a new reference every render. It
    // used to sit raw in `refresh`'s dependency list, so the callback was
    // rebuilt each render, the load effect re-ran, and a component that
    // re-rendered for any unrelated reason issued a query every time.
    const { client, query } = fakeClient();
    const { rerender, renderCount } = await mountWithInlineJoins(client);

    expect(query).toHaveBeenCalledTimes(1);
    const rendersAfterMount = renderCount();

    for (let i = 0; i < 3; i += 1) {
      await act(async () => rerender.current?.());
      await settle();
    }

    // The renders really happened -- otherwise this would pass by doing nothing.
    expect(renderCount()).toBeGreaterThan(rendersAfterMount);
    expect(query).toHaveBeenCalledTimes(1);
  });

  it("still re-queries when the joins content actually changes", async () => {
    // The stabilisation is keyed on the serialised content, so a real change
    // must still get through -- a memo that never invalidates is the other bug.
    const { client, query } = fakeClient();
    const joins = { current: [{ table: "teams", on: "team_id" }] as JoinedRecordsShorthandJoin[] };
    const rerender = { current: null as (() => void) | null };

    function Harness() {
      const [, setTick] = useState(0);
      rerender.current = () => setTick((value) => value + 1);
      useJoinedRecords({ client, podId: "pod-1", baseTable: "members", joins: joins.current });
      return null;
    }

    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    roots.push(root);
    await act(async () => {
      root.render(createElement(Harness));
      await Promise.resolve();
    });
    await settle();
    expect(query).toHaveBeenCalledTimes(1);

    joins.current = [{ table: "teams", on: "team_id", alias: "owning_team" }];
    await act(async () => rerender.current?.());
    await settle();

    expect(query).toHaveBeenCalledTimes(2);
  });
});
