// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { EmailCodeLogin } from "./email-code-login";

let root: Root;
let container: HTMLDivElement;
const fetchCode = vi.fn();
const authenticated = vi.fn();
const back = vi.fn();

beforeEach(async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-09-10T10:00:00Z"));
  vi.stubGlobal("fetch", fetchCode);
  fetchCode.mockReset();
  authenticated.mockReset();
  back.mockReset();
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(<EmailCodeLogin onBack={back} onAuthenticated={authenticated} />));
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});
function response(body: object, ok = true) {
  return { ok, json: async () => body };
}
function button(label: string) {
  return [...container.querySelectorAll("button")].find(item => item.textContent === label)!;
}
async function fill(id: string, value: string) {
  const input = container.querySelector<HTMLInputElement>(`#${id}`)!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
async function submit() {
  await act(async () => container.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
}
async function sentCode() {
  fetchCode.mockResolvedValueOnce(response({ nonce: "browser-nonce" }));
  fetchCode.mockResolvedValueOnce(response({ challenge_id: "challenge-1", expires_at: new Date(Date.now() + 600_000).toISOString() }));
  await fill("email-login-address", "ada@example.com");
  await submit();
}

it("binds the challenge and preserves the entered email after a retryable error", async () => {
  fetchCode.mockRejectedValueOnce(new Error("Connection failed"));
  await fill("email-login-address", "ada@example.com");
  await submit();
  expect(container.querySelector('[role="alert"]')?.textContent).toBe("Connection failed");
  expect(button("Send code").disabled).toBe(false);
  await sentCode();
  expect(JSON.parse(fetchCode.mock.calls.at(-1)![1].body)).toEqual({ email: "ada@example.com", nonce: "browser-nonce" });
  expect(button("Resend in 60s").disabled).toBe(true);
});

it("shows a wrong-code response without losing the challenge", async () => {
  await sentCode();
  await fill("email-login-code", "000000");
  fetchCode.mockResolvedValueOnce(response({ detail: "That code did not match" }, false));
  await submit();
  expect(container.querySelector('[role="alert"]')?.textContent).toBe("That code did not match");
  expect(authenticated).not.toHaveBeenCalled();
  expect(container.querySelector("#email-login-code")).not.toBeNull();
});

it("expires a code, resends after cooldown and lets the person change email", async () => {
  await sentCode();
  await act(async () => vi.advanceTimersByTime(600_000));
  expect(button("Verify and continue").disabled).toBe(true);
  expect(container.querySelector('[role="status"]')?.textContent).toContain("expired");
  fetchCode.mockResolvedValueOnce(response({ challenge_id: "challenge-2", expires_at: new Date(Date.now() + 600_000).toISOString() }));
  await act(async () => button("Resend code").click());
  expect(fetchCode.mock.calls.at(-1)![0]).toContain("/auth/email-code/resend");
  expect(button("Verify and continue").disabled).toBe(false);
  await act(async () => button("Change email").click());
  expect(container.querySelector("#email-login-address")).not.toBeNull();
  expect(container.querySelector("#email-login-code")).toBeNull();
});

it.each(["desktop", "cli", "import"])("returns to existing authenticated navigation with the %s destination intact", async (destination) => {
  window.history.replaceState({}, "", `/auth?redirectTo=${destination}`);
  await sentCode();
  await fill("email-login-code", "012345");
  fetchCode.mockResolvedValueOnce(response({ status: "OK" }));
  await submit();
  expect(authenticated).toHaveBeenCalledOnce();
  expect(window.location.search).toBe(`?redirectTo=${destination}`);
  expect(JSON.parse(fetchCode.mock.calls.at(-1)![1].body)).toEqual({ nonce: "browser-nonce", challenge_id: "challenge-1", code: "012345" });
});
