import { describe, expect, it, vi } from "vitest";

import {
  continueWithEmail,
  emailCodeRequest,
  EmailCodeError,
  GENERIC_FAILURE,
} from "./email-code-client";

type Body = Record<string, unknown>;

function reply(
  body: unknown,
  { ok = true, status = 200, headers = {} as Record<string, string> } = {},
) {
  return {
    ok,
    status,
    headers: { get: (name: string) => headers[name.toLowerCase()] ?? null },
    json: async () => body,
  } as unknown as Response;
}

function fetcherFor(response: Response) {
  return vi.fn<typeof fetch>().mockResolvedValue(response);
}

function sentBody(fetcher: ReturnType<typeof fetcherFor>): Body {
  return JSON.parse(String(fetcher.mock.calls.at(-1)?.[1]?.body));
}

describe("reading the server's own words", () => {
  it("reads `message`, which is the envelope the API actually sends", async () => {
    // Every response from the main app goes through the unified
    // `{message, code, request_id, details}` envelope. Reading only `detail`
    // — which this code did before it was shared — dropped every message this
    // flow can produce, so "The code did not match; try again" reached people
    // as "Unable to continue. Please try again."
    const fetcher = fetcherFor(
      reply(
        { message: "The code did not match; try again", code: "HTTP_400" },
        { ok: false, status: 400 },
      ),
    );
    await expect(emailCodeRequest("verify", {}, fetcher)).rejects.toThrow(
      "The code did not match; try again",
    );
  });

  it("still reads `detail`, for the separately mounted SuperTokens app", async () => {
    const fetcher = fetcherFor(
      reply({ detail: "Login expired; start again in this browser" }, { ok: false, status: 403 }),
    );
    await expect(emailCodeRequest("continue", {}, fetcher)).rejects.toThrow(
      "Login expired; start again in this browser",
    );
  });

  it("falls back to generic copy when a proxy answers in HTML", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue({
      ok: false,
      status: 502,
      headers: { get: () => null },
      json: async () => {
        throw new SyntaxError("Unexpected token <");
      },
    } as unknown as Response);
    await expect(emailCodeRequest("start", {}, fetcher)).rejects.toThrow(
      GENERIC_FAILURE,
    );
  });

  it("tells the person how long to wait when the server said so", async () => {
    const fetcher = fetcherFor(
      reply(
        { message: "Too many code requests; try again later" },
        { ok: false, status: 429, headers: { "retry-after": "90" } },
      ),
    );
    const failure = await emailCodeRequest("continue", {}, fetcher).catch(
      (cause: unknown) => cause,
    );
    expect(failure).toBeInstanceOf(EmailCodeError);
    expect((failure as EmailCodeError).retryAfterSeconds).toBe(90);
    expect((failure as EmailCodeError).message).toContain("2 minutes");
  });

  it("leaves a transport failure as itself", async () => {
    const fetcher = vi.fn<typeof fetch>().mockRejectedValue(new Error("Failed to fetch"));
    await expect(emailCodeRequest("browser", {}, fetcher)).rejects.toThrow(
      "Failed to fetch",
    );
  });
});

describe("continue", () => {
  it("sends the session cookie and the cookie auth mode", async () => {
    const fetcher = fetcherFor(reply({ method: "password" }));
    await continueWithEmail({ email: "ada@example.com", nonce: "n" }, fetcher);
    const [url, init] = fetcher.mock.calls.at(-1)!;
    expect(String(url)).toContain("/auth/email-code/continue");
    expect(init?.credentials).toBe("include");
    expect(
      (init?.headers as Record<string, string>)["st-auth-mode"],
    ).toBe("cookie");
  });

  it("names an abandoned challenge only when there is one to retire", async () => {
    const fetcher = fetcherFor(reply({ method: "password" }));
    await continueWithEmail({ email: "ada@example.com", nonce: "n" }, fetcher);
    expect(sentBody(fetcher)).toEqual({ email: "ada@example.com", nonce: "n" });

    await continueWithEmail(
      { email: "ada@example.com", nonce: "n", abandonChallengeId: "challenge-1" },
      fetcher,
    );
    expect(sentBody(fetcher)).toEqual({
      email: "ada@example.com",
      nonce: "n",
      abandon_challenge_id: "challenge-1",
    });
  });
});
