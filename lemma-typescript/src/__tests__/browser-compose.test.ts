import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import {
  LEMMA_COMPOSE_MESSAGE_TYPE,
  LEMMA_COMPOSE_RESULT_MESSAGE_TYPE,
  canComposeInConversation,
  composeInConversation,
} from "../browser-compose.js";

/** Stand a host in front of the frame: `window.parent` is something else, and
 *  what it does with a message is up to each test. */
function host(onMessage?: (message: any) => void) {
  const parent = {
    postMessage: (message: unknown) => { onMessage?.(message); },
  };
  Object.defineProperty(window, "parent", { value: parent, configurable: true });
  return parent;
}

/** What a host that speaks the protocol does: answer the frame. */
function acknowledging() {
  return host((message: any) => {
    window.dispatchEvent(new MessageEvent("message", {
      data: { type: LEMMA_COMPOSE_RESULT_MESSAGE_TYPE, id: message.id, ok: true },
      source: window.parent as Window,
    }));
  });
}

describe("composeInConversation", () => {
  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(() => {
    vi.useRealTimers();
    Object.defineProperty(window, "parent", { value: window, configurable: true });
  });

  it("offers the text to the host and resolves once the host takes it", async () => {
    const seen: any[] = [];
    host((message) => {
      seen.push(message);
      window.dispatchEvent(new MessageEvent("message", {
        data: { type: LEMMA_COMPOSE_RESULT_MESSAGE_TYPE, id: message.id, ok: true },
        source: window.parent as Window,
      }));
    });
    await expect(composeInConversation("  why is Acme cooling?  ")).resolves.toBe(true);
    expect(seen).toHaveLength(1);
    expect(seen[0].type).toBe(LEMMA_COMPOSE_MESSAGE_TYPE);
    expect(seen[0].text).toBe("why is Acme cooling?");
    expect(seen[0].newConversation).toBe(false);
    expect(typeof seen[0].id).toBe("string");
  });

  it("carries newConversation only when it was asked for", async () => {
    const seen: any[] = [];
    host((message) => {
      seen.push(message);
      window.dispatchEvent(new MessageEvent("message", {
        data: { type: LEMMA_COMPOSE_RESULT_MESSAGE_TYPE, id: message.id, ok: true },
        source: window.parent as Window,
      }));
    });
    await composeInConversation("take Acme on its own", { newConversation: true });
    expect(seen[0].newConversation).toBe(true);
  });

  it("resolves false when nothing is listening, rather than looking like it worked", async () => {
    // The case this exists for: an app opened from a share link. There is no
    // conversation anywhere near it, so a button that silently did nothing
    // would be indistinguishable from one that worked.
    host();
    const offered = composeInConversation("anyone there?");
    await vi.advanceTimersByTimeAsync(2000);
    await expect(offered).resolves.toBe(false);
  });

  it("ignores an acknowledgement for a different offer", async () => {
    host((message) => {
      window.dispatchEvent(new MessageEvent("message", {
        data: { type: LEMMA_COMPOSE_RESULT_MESSAGE_TYPE, id: message.id + "-other", ok: true },
        source: window.parent as Window,
      }));
    });
    const offered = composeInConversation("mine, not that one");
    await vi.advanceTimersByTimeAsync(2000);
    await expect(offered).resolves.toBe(false);
  });

  it("ignores an answer from a frame that is not the host", async () => {
    const sibling = {} as Window;
    host((message) => {
      window.dispatchEvent(new MessageEvent("message", {
        data: { type: LEMMA_COMPOSE_RESULT_MESSAGE_TYPE, id: message.id, ok: true },
        source: sibling,
      }));
    });
    const offered = composeInConversation("only the host answers");
    await vi.advanceTimersByTimeAsync(2000);
    await expect(offered).resolves.toBe(false);
  });

  it("refuses empty text without troubling the host", async () => {
    let asked = 0;
    host(() => { asked += 1; });
    await expect(composeInConversation("   ")).resolves.toBe(false);
    expect(asked).toBe(0);
  });

  it("is unavailable, and silent, when the page is not framed at all", async () => {
    Object.defineProperty(window, "parent", { value: window, configurable: true });
    expect(canComposeInConversation()).toBe(false);
    await expect(composeInConversation("nobody to ask")).resolves.toBe(false);
  });

  it("reports itself available once there is a host to ask", () => {
    acknowledging();
    expect(canComposeInConversation()).toBe(true);
  });
});
