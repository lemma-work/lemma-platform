import { describe, expect, it } from "vitest";

import type { HttpClient } from "../http.js";
import { MachinesNamespace, machinePathRef } from "../namespaces/machines.js";

interface Asked {
  method: string;
  path: string;
  options: { params?: Record<string, unknown>; body?: unknown };
}

function fakeHttp(answers: Record<string, unknown | ((asked: Asked) => unknown)> = {}) {
  const asked: Asked[] = [];
  const http = {
    getBaseUrl: () => "https://api.example.test",
    async request(method: string, path: string, options: Asked["options"] = {}) {
      const entry = { method, path, options };
      asked.push(entry);
      const answer = answers[`${method} ${path}`];
      return typeof answer === "function" ? (answer as (a: Asked) => unknown)(entry) : answer;
    },
  } as unknown as HttpClient;
  return { http, asked };
}

describe("one ref grammar", () => {
  it("names a pod by id or by name, colon-separated", () => {
    expect(machinePathRef("pod:research:qa")).toBe("pod:research:qa");
    expect(machinePathRef("pod:research/qa")).toBe("pod:research:qa");
  });

  it("reads pod:<name> against the pod it is given", () => {
    expect(machinePathRef("pod:qa", "p1")).toBe("pod:p1:qa");
    expect(machinePathRef("pod:p2:qa", "p1")).toBe("pod:p2:qa");
    expect(machinePathRef("me", "p1")).toBe("me");
  });
});

describe("a pod's machines, flat like the Python SDK", () => {
  it("creates in the client's pod unless told another", async () => {
    const { http, asked } = fakeHttp();
    const machines = new MachinesNamespace(http, { podId: () => "p1" });

    await machines.create({ name: "ci", template: "workspace", size: "2x4" });
    await machines.create({ name: "ci", podId: "p2" });

    expect(asked[0]).toMatchObject({
      method: "POST",
      path: "/pods/p1/machines",
      options: { body: { name: "ci", template: "workspace", size: "2x4" } },
    });
    expect(asked[1].path).toBe("/pods/p2/machines");
  });

  it("updates, deletes and reads and sets grants by name", async () => {
    const { http, asked } = fakeHttp();
    const machines = new MachinesNamespace(http, { podId: () => "p1" });

    await machines.update("ci", { size: "4x8" });
    await machines.delete("ci", { podId: "p2" });
    await machines.grants("ci");
    await machines.setGrants("ci", { agents: ["builder"], functions: [] });

    expect(asked.map((a) => `${a.method} ${a.path}`)).toEqual([
      "PATCH /pods/p1/machines/ci",
      "DELETE /pods/p2/machines/ci",
      "GET /pods/p1/machines/ci/grants",
      "PUT /pods/p1/machines/ci/grants",
    ]);
    expect(asked[0].options.body).toEqual({ size: "4x8" });
    expect(asked[3].options.body).toEqual({ agents: ["builder"], functions: [] });
  });

  it("says what is missing when there is no pod", () => {
    const { http } = fakeHttp();
    expect(() => new MachinesNamespace(http).delete("ci")).toThrow(/podId is required/);
  });
});

describe("files", () => {
  it("writes with PUT files:content", async () => {
    const { http, asked } = fakeHttp();
    await new MachinesNamespace(http).files.write("me", "/home/user/lemma/a.txt", "hi");
    expect(asked[0]).toMatchObject({ method: "PUT", path: "/machines/me/files:content" });
  });
});

describe("run", () => {
  it("runs to the end and returns the exit code and what was written", async () => {
    let page = 0;
    const { http } = fakeHttp({
      "POST /machines/me/processes": { id: "proc-1", state: "running" },
      "GET /machines/me/processes/proc-1/output": () =>
        ++page === 1
          ? {
              chunks: [
                { sequence: 1, channel: "stdout", data: "Python " },
                { sequence: 2, channel: "stderr", data: "warn\n" },
                { sequence: 3, channel: "stdout", data: "3.14\n" },
              ],
              next_sequence: 3,
              state: "running",
            }
          : { chunks: [], next_sequence: 3, state: "failed", exit_code: 2 },
    });
    const machines = new MachinesNamespace(http, { WebSocket: null });

    const result = await machines.run("me", { command: "python --version" });

    expect(result).toMatchObject({
      exitCode: 2,
      state: "failed",
      output: "Python warn\n3.14\n",
      stdout: "Python 3.14\n",
      stderr: "warn\n",
      ok: false,
    });
    expect(result.events).toHaveLength(3);
  });
});
