import { afterEach, describe, expect, it, vi } from "vitest";
import type { GeneratedClientAdapter } from "../generated.js";
import { PodSurfacesNamespace } from "../namespaces/pod-surfaces.js";
import { AgentSurfacesService } from "../openapi_client/services/AgentSurfacesService.js";

const passthroughAdapter = { request: (op: () => unknown) => op() } as unknown as GeneratedClientAdapter;

afterEach(() => vi.restoreAllMocks());

describe("Add to Slack", () => {
  it("asks whether Slack is set up, then starts for the organization named", async () => {
    const available = vi
      .spyOn(AgentSurfacesService, "agentSurfaceSlackInstallAvailability")
      .mockResolvedValue({ available: true } as never);
    const start = vi
      .spyOn(AgentSurfacesService, "agentSurfaceSlackInstallStart")
      .mockResolvedValue({ authorization_url: "https://slack.com/oauth/v2/authorize" } as never);
    const surfaces = new PodSurfacesNamespace(passthroughAdapter);

    await expect(surfaces.slackInstallAvailability()).resolves.toEqual({ available: true });
    await surfaces.startSlackInstall({ organization_id: "org-1" });

    expect(available).toHaveBeenCalledWith();
    // The organization travels in the body: "Add to Slack" belongs to no pod.
    expect(start).toHaveBeenCalledWith({ organization_id: "org-1" });
  });
});
