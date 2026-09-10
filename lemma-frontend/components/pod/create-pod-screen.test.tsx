import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

type TestOrg = { id: string; name: string };

const state = vi.hoisted(() => ({
    organizations: [] as TestOrg[],
}));

vi.mock("@/components/dashboard/org-context", () => ({
    useOrganization: () => ({
        organizations: state.organizations,
        currentOrg: state.organizations[0] ?? null,
        setCurrentOrg: vi.fn(),
        isLoading: false,
        hasSession: true,
    }),
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@tanstack/react-query", () => ({
    useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}));
vi.mock("@/lib/sdk/lemma-client", () => ({
    getLemmaClient: () => ({ pods: { create: vi.fn() } }),
}));

import { CreatePodScreen } from "./create-pod-screen";

function render(organizations: TestOrg[]) {
    state.organizations = organizations;
    return renderToStaticMarkup(<CreatePodScreen remixSource={null} />);
}

describe("CreatePodScreen", () => {
    it("names the organization the pod will land in when there is more than one", () => {
        const markup = render([
            { id: "org-1", name: "Acme" },
            { id: "org-2", name: "Side project" },
        ]);

        expect(markup).toContain('aria-label="Organization"');
        expect(markup).toContain("Acme");
    });

    it("leaves out the selector when there is nowhere else the pod could go", () => {
        const markup = render([{ id: "org-1", name: "Acme" }]);

        expect(markup).not.toContain('aria-label="Organization"');
    });
});
