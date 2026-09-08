import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import {
    HarnessKind,
    RuntimeProfileKind,
    RuntimeProfileProtocol,
    RuntimeProfileScope,
    RuntimeProfileStatus,
    type AgentRuntimeProfileResponse,
} from "lemma-sdk";

const state = vi.hoisted(() => ({
    local: true,
    ai: "needs_setup",
    pending: false,
    profiles: [] as AgentRuntimeProfileResponse[],
}));

vi.mock("@/components/dashboard/org-context", () => ({ useOrganization: () => ({ currentOrg: { id: "test-workspace" } }) }));
vi.mock("@/lib/config", () => ({ isLocalDeployment: () => state.local }));
vi.mock("@/lib/desktop/auto-connect", () => ({ useAutoConnectThisComputer: vi.fn() }));
vi.mock("@/lib/desktop/this-computer", () => ({ useThisComputer: () => "this computer" }));
vi.mock("@/lib/desktop/local-capabilities", () => ({
    useLocalAiStatus: () => ({ status: state.ai }),
    openLocalSettings: vi.fn(),
}));
vi.mock("@/lib/hooks/use-agent-runtime", () => ({
    useManagedAgentRuntimes: () => ({ data: { items: state.profiles }, isPending: state.pending }),
}));

import { LocalAiSetupBanner } from "./local-ai-setup-banner";

function profile(availability: string | null): AgentRuntimeProfileResponse {
    return {
        id: "test-profile",
        name: "Test coding agent",
        kind: RuntimeProfileKind.HARNESS,
        status: RuntimeProfileStatus.ACTIVE,
        scope: RuntimeProfileScope.PERSONAL,
        protocol: RuntimeProfileProtocol.AGENT_HOST,
        derived_harness_kind: HarnessKind.HARNESS,
        harness_id: "test-harness",
        availability_status: availability,
    };
}

beforeEach(() => {
    state.local = true;
    state.ai = "needs_setup";
    state.pending = false;
    state.profiles = [];
});

describe("LocalAiSetupBanner", () => {
    it("accepts a ready coding agent without an installation provider", () => {
        state.profiles = [profile("READY")];
        expect(renderToStaticMarkup(<LocalAiSetupBanner />)).toBe("");
    });

    it.each(["OFFLINE", "UNAVAILABLE", "NOT_INSTALLED", null])("routes an unavailable saved agent (%s) to Models", (availability) => {
        state.profiles = [profile(availability)];
        const html = renderToStaticMarkup(<LocalAiSetupBanner />);
        expect(html).toContain("coding agents aren&#x27;t ready");
        expect(html).toContain('href="/organizations/test-workspace/settings/agent-runtimes"');
        expect(html).not.toContain("No model is set up yet");
    });

    it("does not flash a setup warning while saved profiles are loading", () => {
        state.pending = true;
        expect(renderToStaticMarkup(<LocalAiSetupBanner />)).toBe("");
    });

    it("offers initial setup when the only saved agent is archived", () => {
        state.profiles = [{ ...profile("READY"), status: RuntimeProfileStatus.DISABLED }];
        expect(renderToStaticMarkup(<LocalAiSetupBanner />)).toContain("No model is set up yet");
    });

    it("needs no local agent when an installation provider is configured", () => {
        state.ai = "ready";
        expect(renderToStaticMarkup(<LocalAiSetupBanner />)).toBe("");
    });

    it("does not show local-provider setup in cloud mode", () => {
        state.local = false;
        expect(renderToStaticMarkup(<LocalAiSetupBanner />)).toBe("");
    });
});
