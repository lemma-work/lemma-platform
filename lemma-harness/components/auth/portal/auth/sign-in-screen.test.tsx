// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { SignInScreen } from "./sign-in-screen";

let root: Root;
let container: HTMLDivElement;
const fetchCode = vi.fn();
const authenticated = vi.fn();
const signUp = vi.fn();
const signIn = vi.fn();
const redirectToProvider = vi.fn();

const PROVIDERS = [
  { id: "google", name: "Google" },
  { id: "active-directory", name: "Microsoft" },
] as const;

beforeEach(async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-09-10T10:00:00Z"));
  vi.stubGlobal("fetch", fetchCode);
  for (const spy of [fetchCode, authenticated, signUp, signIn, redirectToProvider]) {
    spy.mockReset();
  }
  redirectToProvider.mockResolvedValue({ status: "OK" });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () =>
    root.render(
      <SignInScreen
        providers={PROVIDERS}
        onSignUp={signUp}
        onAuthenticated={authenticated}
        signIn={signIn}
        redirectToProvider={redirectToProvider}
      />,
    ),
  );
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function response(body: object, ok = true) {
  return { ok, json: async () => body, status: ok ? 200 : 400 };
}
function button(label: string) {
  return [...container.querySelectorAll("button")].find(
    (item) => item.textContent === label,
  )!;
}
async function fill(id: string, value: string) {
  const input = container.querySelector<HTMLInputElement>(`#${id}`)!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(
      input,
      value,
    );
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
async function submit() {
  await act(async () =>
    container
      .querySelector("form")!
      .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })),
  );
}
async function continueAs(email: string, answer: object) {
  fetchCode.mockResolvedValueOnce(response({ nonce: "browser-nonce" }));
  fetchCode.mockResolvedValueOnce(response(answer));
  await fill("sign-in-email", email);
  await submit();
}
function lastBody() {
  return JSON.parse(fetchCode.mock.calls.at(-1)![1].body);
}

it("offers every configured provider, and none when there are none", async () => {
  expect(button("Continue with Google")).toBeDefined();
  expect(button("Continue with Microsoft")).toBeDefined();
  await act(async () => button("Continue with Google").click());
  expect(redirectToProvider).toHaveBeenCalledWith("google");

  await act(async () =>
    root.render(
      <SignInScreen
        providers={[]}
        onSignUp={signUp}
        onAuthenticated={authenticated}
        signIn={signIn}
        redirectToProvider={redirectToProvider}
      />,
    ),
  );
  expect(button("Continue with Google")).toBeUndefined();
});

it("says so when a provider button cannot start its handoff", async () => {
  // Fire-and-forget, the button just looks dead: `redirectToProvider` answers
  // ERROR and nothing on screen changes.
  redirectToProvider.mockResolvedValueOnce({ status: "ERROR" });
  await act(async () => button("Continue with Google").click());
  expect(container.querySelector('[role="alert"]')?.textContent).toContain("Google");
  expect(container.querySelector("#sign-in-email")).not.toBeNull();
});

it("asks for a password when the account has one, in one round trip", async () => {
  await continueAs("ada@example.com", { method: "password" });
  expect(fetchCode).toHaveBeenCalledTimes(2);
  expect(String(fetchCode.mock.calls.at(-1)![0])).toContain("/auth/email-code/continue");
  expect(container.querySelector("#sign-in-password")).not.toBeNull();
  // The address stays in the same form, visible and marked as the username, or
  // a password manager has nothing to attach the password to.
  const identity = container.querySelector<HTMLInputElement>("#sign-in-identity")!;
  expect(identity.value).toBe("ada@example.com");
  expect(identity.autocomplete).toBe("username");
  expect(identity.readOnly).toBe(true);
});

it("keeps the password step after a wrong password, without signing anyone in", async () => {
  await continueAs("ada@example.com", { method: "password" });
  signIn.mockResolvedValueOnce({ status: "WRONG_CREDENTIALS_ERROR" });
  await fill("sign-in-password", "nope");
  await submit();
  expect(container.querySelector('[role="alert"]')?.textContent).toContain(
    "doesn’t match",
  );
  expect(authenticated).not.toHaveBeenCalled();
  expect(container.querySelector("#sign-in-password")).not.toBeNull();
});

it("offers a code when a password sign-in is refused as passwordless", async () => {
  await continueAs("ada@example.com", { method: "password" });
  signIn.mockResolvedValueOnce({
    status: "SIGN_IN_NOT_ALLOWED",
    reason: "This email uses email-code login. Choose Continue with email code.",
  });
  await fill("sign-in-password", "nope");
  await submit();
  expect(button("Email me a code instead")).toBeDefined();

  fetchCode.mockResolvedValueOnce(
    response({
      method: "code",
      challenge_id: "challenge-1",
      expires_at: new Date(Date.now() + 600_000).toISOString(),
    }),
  );
  await act(async () => button("Email me a code instead").click());
  expect(container.querySelector("#email-login-code")).not.toBeNull();
});

it("goes straight to the code step, with no separate send", async () => {
  await continueAs("chat@example.com", {
    method: "code",
    challenge_id: "challenge-1",
    expires_at: new Date(Date.now() + 600_000).toISOString(),
  });
  expect(container.querySelector("#email-login-code")).not.toBeNull();
  // `/continue` sent it; a `/start` here would be a second code and a second email.
  expect(
    fetchCode.mock.calls.some((call) => String(call[0]).includes("/auth/email-code/start")),
  ).toBe(false);
  expect(button("Resend in 60s")).toBeDefined();
});

it("retires the abandoned challenge when the address is corrected", async () => {
  await continueAs("typo@example.com", {
    method: "code",
    challenge_id: "challenge-1",
    expires_at: new Date(Date.now() + 600_000).toISOString(),
  });
  await act(async () => button("Change email").click());
  expect(container.querySelector("#sign-in-email")).not.toBeNull();

  fetchCode.mockResolvedValueOnce(response({ method: "password" }));
  await fill("sign-in-email", "fixed@example.com");
  await submit();
  expect(lastBody()).toEqual({
    email: "fixed@example.com",
    nonce: "browser-nonce",
    abandon_challenge_id: "challenge-1",
  });
});

it("signs in with a password without disturbing the destination", async () => {
  window.history.replaceState({}, "", "/auth?redirectTo=desktop");
  await continueAs("ada@example.com", { method: "password" });
  signIn.mockResolvedValueOnce({ status: "OK" });
  await fill("sign-in-password", "correct horse");
  await submit();
  expect(signIn).toHaveBeenCalledWith("ada@example.com", "correct horse");
  expect(authenticated).toHaveBeenCalledOnce();
  expect(window.location.search).toBe("?redirectTo=desktop");
});

it("says how long to wait when the lookup is rate limited", async () => {
  fetchCode.mockResolvedValueOnce(response({ nonce: "browser-nonce" }));
  fetchCode.mockResolvedValueOnce({
    ok: false,
    status: 429,
    headers: { get: (name: string) => (name.toLowerCase() === "retry-after" ? "90" : null) },
    json: async () => ({ message: "Too many code requests; try again later" }),
  });
  await fill("sign-in-email", "ada@example.com");
  await submit();
  expect(container.querySelector('[role="alert"]')?.textContent).toContain("2 minutes");
  expect(container.querySelector("#sign-in-email")).not.toBeNull();
});
