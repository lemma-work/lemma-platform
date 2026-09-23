import { describe, expect, it } from "vitest";

import { activeOrganizationIdFrom } from "./active-organization";

describe("the organization a route names", () => {
    it("reads the id from an organization route and its sub-pages", () => {
        expect(activeOrganizationIdFrom("/organizations/org-1")).toBe("org-1");
        expect(activeOrganizationIdFrom("/organizations/org-1/settings/billing")).toBe(
            "org-1",
        );
    });

    it("names nothing on a route that is not about one organization", () => {
        // The list page is about all of them, so it must not select one.
        expect(activeOrganizationIdFrom("/organizations")).toBeUndefined();
        expect(activeOrganizationIdFrom("/organizations/")).toBeUndefined();
        expect(activeOrganizationIdFrom("/profile/billing")).toBeUndefined();
        // Only a prefix match counts: this is a different route entirely.
        expect(activeOrganizationIdFrom("/pods/organizations/org-1")).toBeUndefined();
    });

    it("tolerates the pathname being absent", () => {
        // `usePathname` is null during the first server render.
        expect(activeOrganizationIdFrom(null)).toBeUndefined();
        expect(activeOrganizationIdFrom(undefined)).toBeUndefined();
    });
});
