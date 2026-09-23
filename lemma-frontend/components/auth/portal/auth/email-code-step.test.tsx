// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { EmailCodeStep } from "./email-code-step";

let root: Root;
let container: HTMLDivElement;
const fetchCode = vi.fn();
const verified = vi.fn();
const changed = vi.fn();

beforeEach(async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-09-10T10:00:00Z"));
  vi.stubGlobal("fetch", fetchCode);
  for (const spy of [fetchCode, verified, changed]) spy.mockReset();
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () =>
    root.render(
      <EmailCodeStep
        email="ada@example.com"
        nonce="browser-nonce"
        initialChallenge={{
          challenge_id: "challenge-1",
          expires_at: new Date(Date.now() + 600_000).toISOString(),
        }}
        onVerified={verified}
        onChangeEmail={changed}
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

function button(label: string) {
  return [...container.querySelectorAll("button")].find(
    (item) => item.textContent === label,
  )!;
}

it("starts the resend cooldown at mount, for either caller", async () => {
  // The one regression this extraction could introduce. Both callers arrive
  // holding a challenge created moments ago -- `EmailCodeLogin` through
  // `/start`, `SignInScreen` because `/continue` sent one on the way -- so a
  // cooldown that waited to be told would offer an instant resend that the
  // server refuses.
  expect(button("Resend in 60s")).toBeDefined();
  expect(button("Resend in 60s").disabled).toBe(true);
});

it("expires the code it was handed and lets a new one be requested", async () => {
  await act(async () => vi.advanceTimersByTime(600_000));
  expect(button("Verify and continue").disabled).toBe(true);
  expect(container.querySelector('[role="status"]')?.textContent).toContain("expired");

  fetchCode.mockResolvedValueOnce({
    ok: true,
    json: async () => ({
      challenge_id: "challenge-2",
      expires_at: new Date(Date.now() + 600_000).toISOString(),
    }),
  });
  await act(async () => button("Resend code").click());
  expect(String(fetchCode.mock.calls.at(-1)![0])).toContain("/auth/email-code/resend");
  expect(button("Verify and continue").disabled).toBe(false);
});

it("refuses a code that is not six digits without spending a request", async () => {
  // `noValidate` turns off the browser's own `required`/`pattern` checks, so a
  // stray Enter would otherwise become a round trip that can only be refused.
  const input = container.querySelector<HTMLInputElement>("#email-login-code")!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(
      input,
      "12",
    );
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () =>
    container
      .querySelector("form")!
      .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })),
  );
  expect(fetchCode).not.toHaveBeenCalled();
  expect(container.querySelector('[role="alert"]')?.textContent).toContain(
    "six-digit",
  );
});
