import { afterEach, describe, expect, it, vi } from "vitest";

import {
    BillingRequestError,
    BillingUnavailableError,
    NoSubscriptionError,
    fetchPersonalSubscription,
    fetchPersonalSubscriptionStatus,
    fetchPlans,
} from "./api";

vi.mock("@/components/auth/portal/auth/config", () => ({
    buildApiUrl: (path: string) => `https://api.test${path}`,
}));

function mockFetch(status: number, body: unknown = {}) {
    const fetchMock = vi.fn().mockResolvedValue({
        ok: status >= 200 && status < 300,
        status,
        statusText: `status ${status}`,
        json: async () => body,
    });
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
}

afterEach(() => {
    vi.unstubAllGlobals();
});

describe("billing availability probe", () => {
    it("reads a 404 on the status route as a deployment without billing", async () => {
        // The open-source backend has no /billing router at all, and this
        // route answers for every authenticated caller when it does exist --
        // so a 404 can only mean the router is absent.
        mockFetch(404);
        await expect(fetchPersonalSubscriptionStatus()).rejects.toBeInstanceOf(
            BillingUnavailableError,
        );
    });

    it("passes the session cookie, since /billing is not a public route", async () => {
        const fetchMock = mockFetch(200, { has_subscription: false });
        await fetchPersonalSubscriptionStatus();
        expect(fetchMock.mock.calls[0][1]).toMatchObject({ credentials: "include" });
    });

    it("does not mistake an auth failure for a missing deployment", async () => {
        // A 401 can happen before the session cookie settles. Reading it as
        // "no billing here" would hide the surface on a deployment that has it.
        mockFetch(401, { detail: "unauthorized" });
        await expect(fetchPersonalSubscriptionStatus()).rejects.toBeInstanceOf(
            BillingRequestError,
        );
    });
});

describe("subscription reads", () => {
    it("reads a 404 on the personal subscription as 'none yet', not as missing billing", async () => {
        // This route legitimately 404s for a user who has never subscribed,
        // which is an ordinary state -- the backend enrols them in free on
        // demand.
        mockFetch(404);
        await expect(fetchPersonalSubscription()).rejects.toBeInstanceOf(
            NoSubscriptionError,
        );
    });

    it("surfaces the API's own error detail rather than a status code", async () => {
        mockFetch(400, { detail: "Plan is no longer available" });
        await expect(fetchPersonalSubscription()).rejects.toThrow(
            "Plan is no longer available",
        );
    });

    it("survives a non-JSON error body", async () => {
        const fetchMock = vi.fn().mockResolvedValue({
            ok: false,
            status: 502,
            statusText: "Bad Gateway",
            json: async () => {
                throw new Error("not json");
            },
        });
        vi.stubGlobal("fetch", fetchMock);
        await expect(fetchPlans()).rejects.toThrow("Bad Gateway");
    });
});

describe("plan queries", () => {
    it("asks the API to filter by plan type instead of filtering client-side", async () => {
        const fetchMock = mockFetch(200, { items: [], next_page_token: null });
        await fetchPlans("TEAM");
        expect(fetchMock.mock.calls[0][0]).toContain("plan_type=TEAM");
        expect(fetchMock.mock.calls[0][0]).toContain("only_active=true");
    });
});
