import { afterEach, describe, expect, it, vi } from "vitest";

describe("auth runtime configuration", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it("lets the trusted Desktop policy override a stale public runtime config", async () => {
    vi.stubGlobal("window", {
      __ENV: {
        NEXT_PUBLIC_AUTH_EMAIL_VERIFICATION_REQUIRED: "true",
        NEXT_PUBLIC_API_URL: "http://app.lemma.localhost:63845",
      },
      __LEMMA_AUTH_CONFIG__: {
        AUTH_EMAIL_VERIFICATION_REQUIRED: "false",
      },
      location: {
        hostname: "app.lemma.localhost",
        origin: "http://app.lemma.localhost:63844",
        port: "63844",
      },
    });

    const { authConfig } = await import("./config");

    expect(authConfig.emailVerificationRequired).toBe(false);
  });
});
