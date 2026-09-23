import { afterEach, describe, expect, it, vi } from "vitest";

import {
    BillingRequestError,
    BillingUnavailableError,
    NoSubscriptionError,
    fetchBillingHistory,
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

/**
 * Pages the API hands back in order, each with the token for the next.
 *
 * Mirrors the keyset paging both billing list routes use: a page carries a
 * token only when another page exists.
 */
function mockPagedFetch(pages: { items: unknown[]; next_page_token: string | null }[]) {
    let call = 0;
    const fetchMock = vi.fn().mockImplementation(async () => {
        const body = pages[Math.min(call, pages.length - 1)];
        call += 1;
        return { ok: true, status: 200, statusText: "OK", json: async () => body };
    });
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
}

describe("paginated billing lists", () => {
    it("reads the catalogue past the first page", async () => {
        // The route answers at most 100 plans. Stopping at the first page
        // meant a plan a customer could have bought was simply not offered.
        const fetchMock = mockPagedFetch([
            { items: [{ id: "a" }], next_page_token: "tok-1" },
            { items: [{ id: "b" }], next_page_token: null },
        ]);

        const plans = await fetchPlans();

        expect(plans.items.map((plan) => plan.id)).toEqual(["a", "b"]);
        expect(plans.next_page_token).toBeNull();
        expect(fetchMock.mock.calls[1][0]).toContain("page_token=tok-1");
        // The filter survives the second request; it is not a bare page fetch.
        expect(fetchMock.mock.calls[1][0]).toContain("only_active=true");
    });

    it("keeps the invoice order the API returned", async () => {
        mockPagedFetch([
            { items: [{ id: "newest" }, { id: "middle" }], next_page_token: "tok-1" },
            { items: [{ id: "oldest" }], next_page_token: null },
        ]);

        const history = await fetchBillingHistory("org-1");

        expect(history.items.map((invoice) => invoice.id)).toEqual([
            "newest",
            "middle",
            "oldest",
        ]);
    });

    it("joins the token to a path that has no query of its own", async () => {
        // Billing history takes no filters, so its first URL has no "?" --
        // appending "&page_token=" would have produced an unparseable query.
        const fetchMock = mockPagedFetch([
            { items: [], next_page_token: "tok-1" },
            { items: [], next_page_token: null },
        ]);

        await fetchBillingHistory("org-1");

        expect(fetchMock.mock.calls[1][0]).toContain("billing-history?page_token=tok-1");
    });

    it("stops following tokens rather than looping forever, and says it stopped", async () => {
        // A server that always returns a token must not spin the client. The
        // unused token is returned so the result does not claim completeness.
        const fetchMock = mockPagedFetch([
            { items: [{ id: "x" }], next_page_token: "endless" },
        ]);

        const plans = await fetchPlans();

        expect(fetchMock).toHaveBeenCalledTimes(20);
        expect(plans.items).toHaveLength(20);
        expect(plans.next_page_token).toBe("endless");
    });

    it("escapes a token that is not URL-safe", async () => {
        const fetchMock = mockPagedFetch([
            { items: [], next_page_token: "a b&c" },
            { items: [], next_page_token: null },
        ]);

        await fetchPlans();

        expect(fetchMock.mock.calls[1][0]).toContain("page_token=a%20b%26c");
    });
});
