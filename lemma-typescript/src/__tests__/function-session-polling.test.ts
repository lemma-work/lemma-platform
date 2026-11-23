import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { LemmaClient } from "../client.js";
import type { FunctionRun } from "../types.js";
import { useFunctionRun, type UseFunctionRunResult } from "../react/index.js";
import { functionRunPollDelay } from "../run-utils.js";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const roots: Root[] = [];

function functionRun(status: string, output: Record<string, unknown> | null = null): FunctionRun {
  return {
    id: "run-1",
    function_id: "fn-1",
    status,
    input_data: {},
    output_data: output,
    created_at: "2026-11-18T12:00:00.000Z",
  } as unknown as FunctionRun;
}

function fakeClient(created: FunctionRun, reads: FunctionRun[]) {
  const create = vi.fn(async () => created);
  const queue = [...reads];
  const get = vi.fn(async () => queue.shift() ?? reads[reads.length - 1] ?? created);
  const client = {
    podId: "pod-1",
    withPod() {
      return this;
    },
    functions: { runs: { create, get, list: vi.fn() } },
  } as unknown as LemmaClient;
  return { client, create, get };
}

async function mount(client: LemmaClient) {
  const hook = { current: null as UseFunctionRunResult | null };

  function Harness() {
    hook.current = useFunctionRun({ client, podId: "pod-1", functionName: "save" });
    return null;
  }

  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  roots.push(root);
  await act(async () => {
    root.render(createElement(Harness));
  });
  return hook;
}

afterEach(async () => {
  vi.useRealTimers();
  while (roots.length > 0) {
    const root = roots.pop();
    if (!root) continue;
    await act(async () => root.unmount());
  }
  document.body.innerHTML = "";
});

describe("running a function from an app", () => {
  it("takes an API function's finished run as the answer, without reading it back", async () => {
    const { client, create, get } = fakeClient(functionRun("COMPLETED", { saved: true }), []);
    const hook = await mount(client);

    await act(async () => {
      await hook.current?.start({ amount: 3 });
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 10));
    });

    expect(create).toHaveBeenCalledTimes(1);
    expect(get).not.toHaveBeenCalled();
    expect(hook.current?.isFinished).toBe(true);
    expect(hook.current?.finalOutput).toEqual({ saved: true });
  });

  it("sees a JOB that finishes within a second without waiting out a two-second poll", async () => {
    vi.useFakeTimers();
    const finishesAt = Date.now() + 600;
    const get = vi.fn(async () =>
      Date.now() >= finishesAt ? functionRun("COMPLETED", { saved: true }) : functionRun("RUNNING"),
    );
    const client = {
      podId: "pod-1",
      withPod() {
        return this;
      },
      functions: { runs: { create: vi.fn(async () => functionRun("PENDING")), get, list: vi.fn() } },
    } as unknown as LemmaClient;
    const hook = await mount(client);

    await act(async () => {
      await hook.current?.start({ amount: 3 });
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(hook.current?.isFinished).toBe(false);

    // Finished at 600 ms; looked at again at 750 ms, not at 2000 ms.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(900);
    });

    expect(hook.current?.isFinished).toBe(true);
    expect(hook.current?.finalOutput).toEqual({ saved: true });
  });
});

describe("functionRunPollDelay", () => {
  it("starts short and doubles up to the configured interval", () => {
    expect([0, 1, 2, 3, 4, 5].map((attempt) => functionRunPollDelay(attempt, 2000))).toEqual([
      250, 500, 1000, 2000, 2000, 2000,
    ]);
  });

  it("never waits longer than an interval shorter than the first step", () => {
    expect(functionRunPollDelay(0, 100)).toBe(100);
    expect(functionRunPollDelay(3, 100)).toBe(100);
  });
});
