import { afterEach, describe, expect, it, vi } from "vitest";
import type { GeneratedClientAdapter } from "../generated.js";
import type { HttpClient } from "../http.js";
import { OrganizationsNamespace } from "../namespaces/organizations.js";
import { OrganizationsService } from "../openapi_client/services/OrganizationsService.js";

const passthroughAdapter = { request: (op: () => unknown) => op() } as unknown as GeneratedClientAdapter;
const noHttp = {} as HttpClient;

afterEach(() => vi.restoreAllMocks());

describe("OrganizationsNamespace overview reads", () => {
  it("reads what waits on you, with the server's default page unless asked", async () => {
    const list = vi.spyOn(OrganizationsService, "orgNeedsYouList").mockResolvedValue({ items: [] } as never);
    const orgs = new OrganizationsNamespace(passthroughAdapter, noHttp);

    await orgs.needsYou("org-1");
    await orgs.needsYou("org-1", { limit: 20 });

    expect(list).toHaveBeenNthCalledWith(1, "org-1", 100);
    expect(list).toHaveBeenNthCalledWith(2, "org-1", 20);
  });

  // Days, chats and the zone are all positional on the wire: a swap of the two
  // numbers type-checks and asks for twenty days of one chat each.
  it("passes the window, the chats per pod and the zone in that order", async () => {
    const get = vi.spyOn(OrganizationsService, "orgRecentsGet").mockResolvedValue({ pods: [] } as never);
    const orgs = new OrganizationsNamespace(passthroughAdapter, noHttp);

    await orgs.recents("org-1");
    await orgs.recents("org-1", { days: 7, chats: 3, timezone: "Europe/Berlin" });

    expect(get).toHaveBeenNthCalledWith(1, "org-1", 14, 10, "UTC");
    expect(get).toHaveBeenNthCalledWith(2, "org-1", 7, 3, "Europe/Berlin");
  });

  it("lists every app across the organization", async () => {
    const list = vi.spyOn(OrganizationsService, "orgAppsList").mockResolvedValue({ items: [] } as never);

    await new OrganizationsNamespace(passthroughAdapter, noHttp).apps("org-1");

    expect(list).toHaveBeenCalledWith("org-1");
  });

  it("reads and marks Home seen, as of now unless given a moment", async () => {
    const get = vi.spyOn(OrganizationsService, "orgHomeSeenGet").mockResolvedValue({ seen_at: null } as never);
    const set = vi.spyOn(OrganizationsService, "orgHomeSeenSet").mockResolvedValue({ seen_at: null } as never);
    const orgs = new OrganizationsNamespace(passthroughAdapter, noHttp);

    await orgs.homeSeen.get("org-1");
    await orgs.homeSeen.mark("org-1");
    await orgs.homeSeen.mark("org-1", "2026-10-23T09:00:00+00:00");

    expect(get).toHaveBeenCalledWith("org-1");
    expect(set).toHaveBeenNthCalledWith(1, "org-1", null);
    expect(set).toHaveBeenNthCalledWith(2, "org-1", { seen_at: "2026-10-23T09:00:00+00:00" });
  });
});
