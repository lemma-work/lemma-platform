import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  CONNECTION_ERROR_CODES,
  ConflictError,
  ConnectionRequiredError,
  ForbiddenError,
  apiErrorFromStatus,
  isConnectionRequiredError,
} from "../index.js";
import { LemmaClient } from "../client.js";
import { clearTestingToken, setTestingToken } from "../auth.js";

/** A pod connector that cannot run for the caller answers with a typed code
 *  and, where the caller can fix it, a `connect_url`. An app should catch one
 *  class for all of them and send the person to connect. */

afterEach(() => {
  clearTestingToken();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const LINK = "https://app.example.invalid/pods/p1/connections?connector=gmail";

describe("ConnectionRequiredError", () => {
  it("is what every typed connector code becomes, whatever its status", () => {
    for (const code of CONNECTION_ERROR_CODES) {
      const error = apiErrorFromStatus(code === "CONNECTION_REQUIRES_PERSON" ? 403 : 409, "no", code, { pod_connector: "gmail" });
      expect(error).toBeInstanceOf(ConnectionRequiredError);
      expect(error).toBeInstanceOf(ApiError);
      expect(isConnectionRequiredError(error)).toBe(true);
      expect((error as ConnectionRequiredError).podConnector).toBe("gmail");
    }
  });

  it("leaves every other refusal its ordinary class", () => {
    expect(apiErrorFromStatus(409, "taken", "POD_CONNECTOR_CONFLICT")).toBeInstanceOf(ConflictError);
    expect(apiErrorFromStatus(403, "no", "FORBIDDEN")).toBeInstanceOf(ForbiddenError);
    expect(isConnectionRequiredError(apiErrorFromStatus(409, "taken"))).toBe(false);
  });

  it("reads the link, the slug and the connector off the details", () => {
    const error = apiErrorFromStatus(409, "Connect your gmail account", "REAUTH_REQUIRED", {
      pod_connector: "gmail", connector_id: "gmail", connect_url: LINK,
    }) as ConnectionRequiredError;
    expect(error.connectUrl).toBe(LINK);
    expect(error.connectorId).toBe("gmail");
    expect(error.isReauth).toBe(true);
    expect(error.statusCode).toBe(409);
    const unfixable = apiErrorFromStatus(409, "turned off", "CONNECTOR_UNAVAILABLE", { pod_connector: "gmail" }) as ConnectionRequiredError;
    expect(unfixable.connectUrl).toBeNull();
    expect(unfixable.connect()).toBe(false);
  });

  it("connect() goes to the link in a top-level page, and to a new tab from inside a frame", () => {
    const error = apiErrorFromStatus(409, "x", "CONNECTION_REQUIRED", { connect_url: LINK }) as ConnectionRequiredError;
    const assign = vi.fn();
    const open = vi.fn();
    const top = { location: { assign }, open } as unknown as Window & { top: unknown };
    (top as { top: unknown }).top = top;
    vi.stubGlobal("window", top);
    expect(error.connect()).toBe(true);
    expect(assign).toHaveBeenCalledWith(LINK);
    expect(open).not.toHaveBeenCalled();

    const framed = { location: { assign }, open, top: {} } as unknown as Window;
    vi.stubGlobal("window", framed);
    expect(error.connect()).toBe(true);
    expect(open).toHaveBeenCalledWith(LINK, "_blank", "noopener");
  });

  it("is what connectors.pod.execute throws for a person with no account", async () => {
    setTestingToken("TOKEN");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      code: "CONNECTION_REQUIRED",
      message: "Connect your gmail account to use 'gmail' in this pod.",
      details: { pod_connector: "gmail", connector_id: "gmail", connect_url: LINK },
    }), { status: 409, headers: { "content-type": "application/json" } })));
    const client = new LemmaClient({ apiUrl: "https://api.test", authUrl: "https://auth.test", podId: "p1" });
    const failure = await client.connectors.pod.execute("gmail", "gmail_send_email", { to: "a@b.c" }).catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(ConnectionRequiredError);
    expect((failure as ConnectionRequiredError).connectUrl).toBe(LINK);
    expect((failure as ConnectionRequiredError).code).toBe("CONNECTION_REQUIRED");
  });
});
