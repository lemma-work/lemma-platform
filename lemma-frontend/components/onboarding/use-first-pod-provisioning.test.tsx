// @vitest-environment jsdom
import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const router = vi.hoisted(() => ({ replace: vi.fn() }));
const queryClient = vi.hoisted(() => ({ invalidateQueries: vi.fn() }));
const podsCreate = vi.hoisted(() => vi.fn());
const toastError = vi.hoisted(() => vi.fn());
const trackPodReady = vi.hoisted(() => vi.fn());

vi.mock("next/navigation", () => ({ useRouter: () => router }));
vi.mock("@tanstack/react-query", () => ({ useQueryClient: () => queryClient }));
vi.mock("sonner", () => ({ toast: { error: toastError } }));
vi.mock("@/lib/sdk/lemma-client", () => ({
    getLemmaClient: () => ({ pods: { create: podsCreate } }),
}));
vi.mock("@/lib/hooks/use-user", () => ({
    useUpdateProfile: () => ({ mutateAsync: vi.fn().mockResolvedValue(undefined) }),
}));
vi.mock("@/lib/analytics/onboarding", () => ({ trackPodReady }));
const ensureOrganization = vi.hoisted(() =>
    vi.fn().mockResolvedValue({ organizationId: "org-1", entryKind: "new_org" }),
);
vi.mock("./use-ensure-organization", () => ({ useEnsureOrganization: () => ensureOrganization }));

import { useFirstPodProvisioning, type FirstPodProvisioning } from "./use-first-pod-provisioning";

let root: Root;
let container: HTMLDivElement;

/**
 * The hook's two outputs, reachable from the test: the state through the DOM,
 * and `open` through a handle the harness publishes after each render.
 */
const handle: { open: FirstPodProvisioning["open"] | null } = { open: null };

function Harness() {
    const provisioning: FirstPodProvisioning = useFirstPodProvisioning({
        enabled: true,
        profile: { email: "ada@example.com", first_name: "Ada", last_name: "Lovelace" },
        organizations: [],
        suggestedOrganization: null,
    });
    useEffect(() => {
        handle.open = provisioning.open;
    });
    return <span data-state={provisioning.state} />;
}

const open = () =>
    act(async () => {
        handle.open!();
    });

/** A pod creation the test finishes by hand, so "before" and "after" are real. */
function pendingCreate() {
    let finish!: (pod: { id: string }) => void;
    let fail!: (error: Error) => void;
    podsCreate.mockImplementationOnce(
        () =>
            new Promise((resolve, reject) => {
                finish = resolve;
                fail = reject;
            }),
    );
    return {
        finish: (id: string) => act(async () => finish({ id })),
        fail: (message: string) => act(async () => fail(new Error(message))),
    };
}

const state = () => container.querySelector("span")?.getAttribute("data-state");

beforeEach(() => {
    vi.clearAllMocks();
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
});

afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
});

async function mount() {
    await act(async () => root.render(<Harness />));
}

describe("first-pod provisioning holds the door until asked", () => {
    it("reports ready without navigating when nobody has asked to go in", async () => {
        const create = pendingCreate();
        await mount();
        expect(state()).toBe("running");
        await create.finish("pod-1");
        expect(state()).toBe("ready");
        expect(router.replace).not.toHaveBeenCalled();
        expect(trackPodReady).toHaveBeenCalledWith("new_org", null);
        expect(queryClient.invalidateQueries).toHaveBeenCalledWith({ queryKey: ["pods"] });
    });

    it("goes in when asked after the pod exists, behind the welcome door", async () => {
        const create = pendingCreate();
        await mount();
        await create.finish("pod-1");
        await open();
        expect(state()).toBe("navigated");
        expect(router.replace).toHaveBeenCalledTimes(1);
        const [href] = router.replace.mock.calls[0];
        expect(href).toContain("/pod/pod-1/conversations/new?");
        expect(href).toContain("welcome=1");
        expect(href).toContain("first_run");
        // Asking again is not a second navigation.
        await open();
        expect(router.replace).toHaveBeenCalledTimes(1);
    });

    it("remembers an early ask and goes in the moment the pod exists", async () => {
        const create = pendingCreate();
        await mount();
        await open();
        expect(router.replace).not.toHaveBeenCalled();
        expect(state()).toBe("running");
        await create.finish("pod-2");
        expect(state()).toBe("navigated");
        expect(router.replace).toHaveBeenCalledTimes(1);
        expect(router.replace.mock.calls[0][0]).toContain("/pod/pod-2/");
    });

    it("says what went wrong and steps aside, rather than opening nothing", async () => {
        const create = pendingCreate();
        await mount();
        await create.fail("Name already taken");
        expect(state()).toBe("failed");
        expect(toastError).toHaveBeenCalledWith(
            "Could not finish setting up your workspace: Name already taken",
        );
        await open();
        expect(router.replace).not.toHaveBeenCalled();
    });
});
