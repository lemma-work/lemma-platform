import { beforeEach, describe, expect, it, vi } from "vitest";

type PreAPIHook = (context: { action: string; requestInit: RequestInit; url: string }) => Promise<unknown>;

const init = vi.fn();
const sessionInit = vi.fn((config: unknown) => config);

vi.mock("supertokens-web-js", () => ({ default: { init: (config: unknown) => init(config) } }));
vi.mock("supertokens-web-js/recipe/session/index.js", () => ({
  default: { init: (config: unknown) => sessionInit(config) },
}));

describe("ensureCookieSessionSupport refresh guard", () => {
  beforeEach(() => {
    vi.resetModules();
    init.mockClear();
    sessionInit.mockClear();
  });

  async function start(onUnauthorised: () => void): Promise<PreAPIHook> {
    const { ensureCookieSessionSupport } = await import("../supertokens.js");
    ensureCookieSessionSupport("https://api.x.test", onUnauthorised);
    const config = sessionInit.mock.calls[0]?.[0] as { preAPIHook?: PreAPIHook } | undefined;
    if (!config?.preAPIHook) throw new Error("Session.init was not given a preAPIHook");
    return config.preAPIHook;
  }

  it("refuses a storm of refreshes and tells the app the session is unusable", async () => {
    const onUnauthorised = vi.fn();
    const hook = await start(onUnauthorised);
    const refresh = { action: "REFRESH_SESSION", requestInit: {}, url: "https://api.x.test/st/auth/session/refresh" };

    for (let attempt = 0; attempt < 4; attempt += 1) await expect(hook(refresh)).resolves.toBe(refresh);
    await expect(hook(refresh)).rejects.toMatchObject({ name: "RefreshSuspendedError" });
    await expect(hook(refresh)).rejects.toMatchObject({ name: "RefreshSuspendedError" });

    expect(onUnauthorised).toHaveBeenCalledTimes(1);
  });

  it("never holds back anything that is not a refresh", async () => {
    const hook = await start(vi.fn());
    const signOut = { action: "SIGN_OUT", requestInit: {}, url: "https://api.x.test/st/auth/signout" };
    for (let attempt = 0; attempt < 20; attempt += 1) await expect(hook(signOut)).resolves.toBe(signOut);
  });
});
