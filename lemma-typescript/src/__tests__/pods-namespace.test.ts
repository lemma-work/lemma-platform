import { afterEach, describe, expect, it, vi } from "vitest";
import type { GeneratedClientAdapter } from "../generated.js";
import type { HttpClient } from "../http.js";
import { PodsNamespace } from "../namespaces/pods.js";
import { PodJoinPolicy } from "../openapi_client/models/PodJoinPolicy.js";
import { PodsService } from "../openapi_client/services/PodsService.js";

const passthroughAdapter = { request: (op: () => unknown) => op() } as unknown as GeneratedClientAdapter;
// None of these calls goes through the raw HTTP client.
const unusedHttp = {} as HttpClient;

afterEach(() => vi.restoreAllMocks());

describe("PodsNamespace", () => {
  it("lists the pods the caller can join, a page at a time", async () => {
    const listSpy = vi
      .spyOn(PodsService, "podListJoinable")
      .mockResolvedValue({ items: [], limit: 100, total: 0 } as never);
    const pods = new PodsNamespace(passthroughAdapter, unusedHttp);

    await pods.listJoinable("org-1");
    await pods.listJoinable("org-1", { limit: 20, pageToken: "pod-9" });

    expect(listSpy).toHaveBeenNthCalledWith(1, "org-1", 100, undefined);
    expect(listSpy).toHaveBeenNthCalledWith(2, "org-1", 20, "pod-9");
  });

  it("creates a pod with who may join it and whether new members join it", async () => {
    const createSpy = vi
      .spyOn(PodsService, "podCreate")
      .mockResolvedValue({ id: "pod-1" } as never);
    const pods = new PodsNamespace(passthroughAdapter, unusedHttp);

    await pods.create({
      organization_id: "org-1",
      name: "Launch room",
      config: { join_policy: PodJoinPolicy.ORG_MEMBERS, auto_join_new_members: true },
    });

    expect(createSpy).toHaveBeenCalledWith({
      organization_id: "org-1",
      name: "Launch room",
      config: { join_policy: "ORG_MEMBERS", auto_join_new_members: true },
    });
  });

  it("joins a pod open to the caller", async () => {
    const joinSpy = vi
      .spyOn(PodsService, "podJoin")
      .mockResolvedValue({ pod_member_id: "member-1" } as never);
    const pods = new PodsNamespace(passthroughAdapter, unusedHttp);

    await pods.join("pod-1");

    expect(joinSpy).toHaveBeenCalledWith("pod-1");
  });
});
