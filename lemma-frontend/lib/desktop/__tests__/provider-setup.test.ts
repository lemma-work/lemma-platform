// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { configureAiProvider, discoverProviderModels } from "../local-capabilities";
import { useLocalProviderSetup, type ProviderPreset } from "../provider-setup";

vi.mock("../local-capabilities", () => ({
    configureAiProvider: vi.fn(),
    discoverProviderModels: vi.fn(),
}));

const provider: ProviderPreset = {
    id: "first", title: "First provider", hint: "Test provider", protocol: "openai_compat",
    baseUrl: "https://first.example.test/v1", needsKey: true,
};
const otherProvider = { ...provider, id: "second", title: "Second provider", baseUrl: "https://second.example.test/v1" };

function deferred<T>() {
    let resolve!: (value: T) => void;
    let reject!: (error: Error) => void;
    const promise = new Promise<T>((accept, refuse) => { resolve = accept; reject = refuse; });
    return { promise, resolve, reject };
}

beforeEach(() => vi.resetAllMocks());
afterEach(cleanup);

function setup() {
    const hook = renderHook(useLocalProviderSetup);
    act(() => hook.result.current.selectPreset(provider));
    act(() => hook.result.current.setApiKey("test credential"));
    return hook;
}

describe("provider setup", () => {
    it("discards a model list from a provider the user switched away from", async () => {
        const old = deferred<string[]>();
        vi.mocked(discoverProviderModels).mockReturnValueOnce(old.promise).mockResolvedValueOnce(["second-model"]);
        const { result } = setup();
        let first!: Promise<void>;
        act(() => { first = result.current.listModels(); });
        act(() => result.current.selectPreset(otherProvider));
        act(() => result.current.setApiKey("second test credential"));
        await act(() => result.current.listModels());
        await act(async () => { old.resolve(["first-model"]); await first; });
        expect(result.current.models).toEqual(["second-model"]);
        expect(result.current.model).toBe("second-model");
        expect(result.current.listing).toBe(false);
        expect(discoverProviderModels).toHaveBeenLastCalledWith(expect.objectContaining({ base_url: otherProvider.baseUrl }), "second test credential");
    });

    it("discards an old error without stopping the newer request's progress", async () => {
        const old = deferred<string[]>();
        const next = deferred<string[]>();
        vi.mocked(discoverProviderModels).mockReturnValueOnce(old.promise).mockReturnValueOnce(next.promise);
        const { result } = setup();
        let first!: Promise<void>;
        let second!: Promise<void>;
        act(() => { first = result.current.listModels(); second = result.current.listModels(); });
        await act(async () => { old.reject(new Error("old endpoint unavailable")); await first; });
        expect(result.current.error).toBeNull();
        expect(result.current.listing).toBe(true);
        await act(async () => { next.resolve(["new-model"]); await second; });
        expect(result.current.model).toBe("new-model");
    });

    it("invalidates discovered models when the credential changes", async () => {
        vi.mocked(discoverProviderModels).mockResolvedValue(["first-model"]);
        const { result } = setup();
        await act(() => result.current.listModels());
        act(() => result.current.setApiKey("replacement test credential"));
        expect(result.current.models).toEqual([]);
        await act(() => result.current.apply());
        expect(configureAiProvider).not.toHaveBeenCalled();
    });

    it("keeps failed apply state and allows a deliberate retry", async () => {
        vi.mocked(discoverProviderModels).mockResolvedValue(["first-model"]);
        vi.mocked(configureAiProvider).mockRejectedValueOnce(new Error("Provider could not be activated")).mockResolvedValueOnce();
        const { result } = setup();
        await act(() => result.current.listModels());
        await act(async () => { expect(await result.current.apply()).toBe(false); });
        expect(result.current.error).toBe("Provider could not be activated");
        expect(result.current.apiKey).toBe("test credential");
        expect(result.current.model).toBe("first-model");
        await act(async () => { expect(await result.current.apply()).toBe(true); });
        expect(result.current.error).toBeNull();
        expect(configureAiProvider).toHaveBeenCalledTimes(2);
    });

    it("admits only one apply and freezes its provider draft until it finishes", async () => {
        vi.mocked(discoverProviderModels).mockResolvedValue(["first-model", "other-model"]);
        const pending = deferred<void>();
        vi.mocked(configureAiProvider).mockReturnValue(pending.promise);
        const { result } = setup();
        await act(() => result.current.listModels());
        let first!: Promise<boolean>;
        let second!: Promise<boolean>;
        act(() => {
            first = result.current.apply();
            second = result.current.apply();
            result.current.selectPreset(otherProvider);
            result.current.setApiKey("changed test credential");
            result.current.setModel("other-model");
        });
        expect(await second).toBe(false);
        expect(result.current.preset?.id).toBe(provider.id);
        expect(result.current.apiKey).toBe("test credential");
        expect(result.current.model).toBe("first-model");
        expect(configureAiProvider).toHaveBeenCalledOnce();
        await act(async () => { pending.resolve(); expect(await first).toBe(true); });
        expect(result.current.applying).toBe(false);
    });

    it("refuses applying the old model while a new discovery starts in the same event", async () => {
        const pending = deferred<string[]>();
        vi.mocked(discoverProviderModels).mockResolvedValueOnce(["first-model"]).mockReturnValueOnce(pending.promise);
        const { result } = setup();
        await act(() => result.current.listModels());
        let listing!: Promise<void>;
        await act(async () => {
            listing = result.current.listModels();
            expect(await result.current.apply()).toBe(false);
        });
        expect(configureAiProvider).not.toHaveBeenCalled();
        await act(async () => { pending.resolve(["replacement-model"]); await listing; });
    });

    it("reports an empty model list and cannot apply an arbitrary model", async () => {
        vi.mocked(discoverProviderModels).mockResolvedValue([]);
        const { result } = setup();
        await act(() => result.current.listModels());
        act(() => result.current.setModel("invented-model"));
        expect(result.current.model).toBe("");
        expect(result.current.error).toContain("reported no models");
        await act(() => result.current.apply());
        expect(configureAiProvider).not.toHaveBeenCalled();
    });

    it("does not report success after the setup screen has unmounted", async () => {
        vi.mocked(discoverProviderModels).mockResolvedValue(["first-model"]);
        const pending = deferred<void>();
        vi.mocked(configureAiProvider).mockReturnValue(pending.promise);
        const { result, unmount } = setup();
        await act(() => result.current.listModels());
        let applying!: Promise<boolean>;
        act(() => { applying = result.current.apply(); });
        unmount();
        pending.resolve();
        expect(await applying).toBe(false);
    });
});
