/**
 * @vitest-environment jsdom
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  appendQueuedSteer,
  clearQueuedSteers,
  readQueuedSteers,
  removeQueuedSteer,
  writeQueuedSteers,
} from "../react/queued-steers.js";

const CONVERSATION = "conv-1";

afterEach(() => {
  // Mocks first: one test replaces the `localStorage` getter with one that
  // throws, and clearing before restoring makes teardown itself throw.
  vi.restoreAllMocks();
  window.localStorage.clear();
});

describe("queued steers", () => {
  it("keeps what was queued, oldest first, across a reload", () => {
    appendQueuedSteer(CONVERSATION, "also check the invoices");
    appendQueuedSteer(CONVERSATION, "one more thing");

    // A reload is exactly this: nothing in memory, everything from storage.
    expect(readQueuedSteers(CONVERSATION).map((item) => item.content)).toEqual([
      "also check the invoices",
      "one more thing",
    ]);
  });

  it("keeps each conversation's queue to itself", () => {
    appendQueuedSteer(CONVERSATION, "mine");
    appendQueuedSteer("conv-2", "theirs");

    expect(readQueuedSteers(CONVERSATION).map((i) => i.content)).toEqual(["mine"]);
    expect(readQueuedSteers("conv-2").map((i) => i.content)).toEqual(["theirs"]);
  });

  it("removes one without disturbing the rest", () => {
    appendQueuedSteer(CONVERSATION, "first");
    const second = appendQueuedSteer(CONVERSATION, "second")[1];

    const left = removeQueuedSteer(CONVERSATION, second.id);

    expect(left.map((i) => i.content)).toEqual(["first"]);
    expect(readQueuedSteers(CONVERSATION).map((i) => i.content)).toEqual(["first"]);
  });

  it("leaves no empty key behind once the queue drains", () => {
    appendQueuedSteer(CONVERSATION, "only");
    clearQueuedSteers(CONVERSATION);

    expect(readQueuedSteers(CONVERSATION)).toEqual([]);
    expect(window.localStorage.getItem("lemma.queued-steers.conv-1")).toBeNull();
  });

  it("drops a corrupt entry rather than the whole queue", () => {
    // Written by another version of the app, or half-written.
    window.localStorage.setItem(
      "lemma.queued-steers.conv-1",
      JSON.stringify([{ id: "a", content: "kept", queuedAt: "t" }, { id: "b" }, "nonsense"]),
    );

    expect(readQueuedSteers(CONVERSATION).map((i) => i.content)).toEqual(["kept"]);
  });

  it("survives storage that is not JSON at all", () => {
    window.localStorage.setItem("lemma.queued-steers.conv-1", "{oh dear");
    expect(readQueuedSteers(CONVERSATION)).toEqual([]);
  });

  it("still works when the accessor itself throws", () => {
    // A browser set to block site data throws on `window.localStorage`, not on
    // the call. Guarding only the call left the queue crashing the composer.
    const spy = vi.spyOn(window, "localStorage", "get").mockImplementation(() => {
      throw new Error("blocked");
    });

    expect(readQueuedSteers(CONVERSATION)).toEqual([]);
    expect(() => writeQueuedSteers(CONVERSATION, [])).not.toThrow();
    expect(() => appendQueuedSteer(CONVERSATION, "x")).not.toThrow();
    expect(spy).toHaveBeenCalled();
  });

  it("gives every queued message its own id", () => {
    appendQueuedSteer(CONVERSATION, "a");
    appendQueuedSteer(CONVERSATION, "b");
    appendQueuedSteer(CONVERSATION, "a");

    const queue = readQueuedSteers(CONVERSATION);
    // Three entries including a repeated message: removing one must not take
    // its twin with it, so the ids cannot be derived from the content.
    expect(queue).toHaveLength(3);
    expect(new Set(queue.map((item) => item.id)).size).toBe(3);
  });
});
