// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AssistantQueuedSteers } from "./assistant-queued-steers";

afterEach(cleanup);

const item = (id: string, content: string) => ({ id, content, queuedAt: "2026-09-10T00:00:00Z" });

describe("messages queued for a turn that cannot hear them", () => {
  it("says nothing when nothing is queued", () => {
    const { container } = render(<AssistantQueuedSteers items={[]} />);
    expect(container.innerHTML).toBe("");
  });

  it("shows the message as queued rather than sent", () => {
    render(<AssistantQueuedSteers items={[item("a", "also check the invoices")]} />);

    expect(screen.getByText("also check the invoices")).toBeTruthy();
    expect(screen.getByText("Queued")).toBeTruthy();
    // The sentence is the whole point: the message did not go where the person
    // expected, and this is the only place that says why.
    expect(screen.getByText(/Will be sent when this turn finishes/)).toBeTruthy();
  });

  it("counts them when more than one is waiting", () => {
    render(<AssistantQueuedSteers items={[item("a", "one"), item("b", "two")]} />);
    expect(screen.getByText(/2 messages will be sent/)).toBeTruthy();
  });

  it("offers the interrupt, and says what it costs", () => {
    const onSendNow = vi.fn();
    render(<AssistantQueuedSteers items={[item("a", "do this instead")]} onSendNow={onSendNow} />);

    const button = screen.getByRole("button", { name: "Send now" });
    expect(button.getAttribute("title")).toBe("Stop the current turn and send this now");
    button.click();
    expect(onSendNow).toHaveBeenCalledTimes(1);
  });

  it("names each remove button by the message it drops", () => {
    // Two queued messages used to give a screen reader two buttons both called
    // "Remove", neither of which said what it would discard.
    const onDiscard = vi.fn();
    render(
      <AssistantQueuedSteers items={[item("a", "first"), item("b", "second")]} onDiscard={onDiscard} />,
    );

    screen.getByRole("button", { name: "Remove queued message: second" }).click();

    expect(onDiscard).toHaveBeenCalledWith("b");
    expect(screen.getByRole("button", { name: "Remove queued message: first" })).toBeTruthy();
  });

  it("offers no controls that were not wired up", () => {
    render(<AssistantQueuedSteers items={[item("a", "orphan")]} />);
    expect(screen.queryByRole("button", { name: "Send now" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Remove queued message/ })).toBeNull();
  });

  it("announces itself, since the person is waiting on an answer", () => {
    render(<AssistantQueuedSteers items={[item("a", "waiting")]} />);
    const region = screen.getByRole("status");
    expect(region.getAttribute("aria-live")).toBe("polite");
  });
});
