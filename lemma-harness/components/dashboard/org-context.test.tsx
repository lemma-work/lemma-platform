// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OrganizationProvider, useOrganization } from "./org-context";

const pathname = vi.hoisted(() => ({ value: "/" }));
const organizations = vi.hoisted(() => ({
    value: [] as { id: string; name: string }[],
}));

vi.mock("next/navigation", () => ({
    usePathname: () => pathname.value,
}));

vi.mock("@/lib/hooks/use-lemma-auth", () => ({
    useLemmaAuth: () => ({ isAuthenticated: true, isLoading: false }),
}));

vi.mock("@/lib/hooks/use-organizations", () => ({
    useOrganizations: () => ({
        data: { items: organizations.value },
        isLoading: false,
    }),
}));

function CurrentOrg() {
    const { currentOrg } = useOrganization();
    return <span data-testid="current">{currentOrg?.id ?? "none"}</span>;
}

function renderProvider() {
    render(
        <OrganizationProvider>
            <CurrentOrg />
        </OrganizationProvider>,
    );
    return screen.getByTestId("current").textContent;
}

beforeEach(() => {
    window.localStorage.clear();
    pathname.value = "/";
    organizations.value = [
        { id: "org-a", name: "A" },
        { id: "org-b", name: "B" },
    ];
});

afterEach(cleanup);

describe("which organization the app is acting on", () => {
    it("follows the route rather than the last one used", () => {
        // Following a direct link to B's settings while A was remembered left
        // "current" on A -- and `CreatePodScreen` sends `currentOrg.id`, so a
        // pod created from that page was filed under the wrong organization.
        window.localStorage.setItem("lemma:selected-org-id", "org-a");
        pathname.value = "/organizations/org-b/settings/pods";

        expect(renderProvider()).toBe("org-b");
    });

    it("settles on the route's organization in the first render", () => {
        // Copying the route into state from an effect showed one frame of the
        // remembered organization first, which is long enough for a child to
        // fire a request against it.
        window.localStorage.setItem("lemma:selected-org-id", "org-a");
        pathname.value = "/organizations/org-b";
        const seen: (string | undefined)[] = [];

        function Recorder() {
            const { currentOrg } = useOrganization();
            seen.push(currentOrg?.id);
            return null;
        }

        render(
            <OrganizationProvider>
                <Recorder />
            </OrganizationProvider>,
        );

        expect(seen).not.toContain("org-a");
        expect(seen.at(-1)).toBe("org-b");
    });

    it("ignores an organization in the URL that the user is not in", () => {
        // A pasted or guessed id must not become the organization the rest of
        // the app acts on; the backend would refuse it, in a place the user
        // never asked to be.
        //
        // Deliberately not the first organization in the list: an unreachable
        // id falls through to `organizations[0]`, so remembering org-a here
        // would pass whether or not the id was rejected.
        window.localStorage.setItem("lemma:selected-org-id", "org-b");
        pathname.value = "/organizations/org-somebody-elses";

        expect(renderProvider()).toBe("org-b");
        // And it is still what gets remembered -- an id we refused to act on
        // must not evict the one we are acting on.
        expect(window.localStorage.getItem("lemma:selected-org-id")).toBe("org-b");
    });

    it("keeps the route's organization after navigating away from it", () => {
        // Starts on a different remembered organization, so persisting the
        // remembered id rather than the route's is a visible difference.
        window.localStorage.setItem("lemma:selected-org-id", "org-a");
        pathname.value = "/organizations/org-b";
        renderProvider();
        cleanup();
        pathname.value = "/profile/billing";

        expect(renderProvider()).toBe("org-b");
        expect(window.localStorage.getItem("lemma:selected-org-id")).toBe("org-b");
    });
});
