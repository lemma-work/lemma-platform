import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ConversationMessage } from "../types.js";
import {
  useAssistantRuntime,
  type UseAssistantRuntimeResult,
} from "../react/useAssistantRuntime.js";

// A provisional turn is stamped from the DEVICE clock and its echo from the
// SERVER clock, so the store cannot pair them by comparing the two: on a
// machine whose clock is wrong — Windows with a dead time service, a dual-boot
// box reading the RTC as local time, a VM resumed from sleep — the gap is the
// skew, not the round-trip. The echo used to be filed as a second message, and
// the sender watched their own message appear twice: once at the time their
// machine believes it is, once where the server put it.

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const roots: Root[] = [];

afterEach(() => {
  vi.useRealTimers();
  act(() => {
    roots.splice(0).forEach((root) => root.unmount());
  });
});

const CONVERSATION_ID = "conv-1";
/** What the server's clock reads while the test runs. */
const SERVER_NOW = new Date("2026-08-30T12:00:00.000Z");

function serverEcho(id: string, text: string): ConversationMessage {
  return {
    id,
    role: "user",
    kind: "TEXT",
    text,
    created_at: SERVER_NOW.toISOString(),
    conversation_id: CONVERSATION_ID,
  } as ConversationMessage;
}

function mountRuntime() {
  let latest: UseAssistantRuntimeResult | null = null;
  const container = document.createElement("div");
  const root = createRoot(container);
  roots.push(root);

  function Probe() {
    latest = useAssistantRuntime({ conversationId: CONVERSATION_ID });
    return null;
  }

  act(() => {
    root.render(createElement(Probe));
  });

  return {
    get current(): UseAssistantRuntimeResult {
      if (!latest) throw new Error("Runtime is not mounted.");
      return latest;
    },
    userMessages(text: string) {
      return this.current.runtimeMessages.filter(
        (message) => message.role === "user" && message.text === text,
      );
    },
  };
}

/** Send a turn from a device whose clock is `skewMs` away from the server's,
 *  then let the server's echo of it arrive. */
function sendWithSkew(skewMs: number, text: string) {
  vi.useFakeTimers();
  vi.setSystemTime(new Date(SERVER_NOW.getTime() + skewMs));

  const runtime = mountRuntime();
  let provisionalId = "";
  act(() => {
    provisionalId = runtime.current.appendOptimisticUserMessage(text, {
      conversationId: CONVERSATION_ID,
    }).id;
  });
  act(() => {
    runtime.current.mergeMessages([serverEcho("srv-1", text)]);
  });

  return { runtime, provisionalId };
}

const MINUTE = 60 * 1000;

describe("a sender whose device clock is wrong", () => {
  it("sees one message, not two, when the clock runs hours fast", () => {
    const { runtime } = sendWithSkew(3 * 60 * MINUTE, "ship it");
    expect(runtime.userMessages("ship it")).toHaveLength(1);
  });

  it("sees one message, not two, when the clock runs days slow", () => {
    const { runtime } = sendWithSkew(-2 * 24 * 60 * MINUTE, "ship it");
    expect(runtime.userMessages("ship it")).toHaveLength(1);
  });

  it("keeps the turn's identity across the echo, so it does not remount", () => {
    const { runtime, provisionalId } = sendWithSkew(3 * 60 * MINUTE, "ship it");
    const [only] = runtime.userMessages("ship it");
    expect(only?.id).toBe("srv-1");
    expect((only as { optimistic_id?: string } | undefined)?.optimistic_id).toBe(provisionalId);
  });

  // The other half of a wrong clock. Even paired correctly, a turn stamped from
  // a device running days behind sorts before the entire transcript — the
  // sender's own message opens the conversation until the echo lands and moves
  // it. It has to go last from the moment it is shown.
  it("still sends the turn to the end of a transcript it did not stamp", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(SERVER_NOW.getTime() - 2 * 24 * 60 * MINUTE));

    const runtime = mountRuntime();
    act(() => {
      runtime.current.mergeMessages([
        serverEcho("srv-old", "what did we decide?"),
        {
          id: "srv-old-reply",
          role: "assistant",
          kind: "TEXT",
          text: "We shipped it.",
          created_at: SERVER_NOW.toISOString(),
          conversation_id: CONVERSATION_ID,
        } as ConversationMessage,
      ]);
    });
    act(() => {
      runtime.current.appendOptimisticUserMessage("and after that?", {
        conversationId: CONVERSATION_ID,
      });
    });

    const transcript = runtime.current.runtimeMessages.map((message) => message.text);
    expect(transcript).toEqual(["what did we decide?", "We shipped it.", "and after that?"]);
  });
});

// Pairing used to break a tie between two provisional turns of the same text by
// picking whichever sat closest in time. That tie-break is gone with the clocks,
// and store order replaces it — which on a device running behind is order alone,
// because every provisional turn anchors to the same server message and they all
// come out holding the identical stamp.
describe("two turns of the same text, both in flight", () => {
  it("pairs each echo with the turn that was actually sent first", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(SERVER_NOW.getTime() - 2 * 24 * 60 * MINUTE));

    const runtime = mountRuntime();
    act(() => {
      runtime.current.mergeMessages([
        {
          id: "srv-prompt",
          role: "assistant",
          kind: "TEXT",
          text: "Ready when you are.",
          created_at: SERVER_NOW.toISOString(),
          conversation_id: CONVERSATION_ID,
        } as ConversationMessage,
      ]);
    });

    let firstId = "";
    let secondId = "";
    act(() => {
      firstId = runtime.current.appendOptimisticUserMessage("go", { conversationId: CONVERSATION_ID }).id;
      secondId = runtime.current.appendOptimisticUserMessage("go", { conversationId: CONVERSATION_ID }).id;
    });
    expect(runtime.userMessages("go").map((message) => message.id)).toEqual([firstId, secondId]);

    act(() => {
      runtime.current.mergeMessages([serverEcho("srv-go-1", "go")]);
    });
    act(() => {
      runtime.current.mergeMessages([
        { ...serverEcho("srv-go-2", "go"), created_at: new Date(SERVER_NOW.getTime() + 1000).toISOString() } as ConversationMessage,
      ]);
    });

    const settled = runtime.userMessages("go");
    expect(settled.map((message) => message.id)).toEqual(["srv-go-1", "srv-go-2"]);
    expect(settled.map((message) => (message as { optimistic_id?: string }).optimistic_id))
      .toEqual([firstId, secondId]);
  });
});

// Pairing is by text, so the reason it cannot pair a provisional turn with some
// older message that happens to read the same is recency — and recency is now
// judged on the server's timeline. That has to keep holding.
describe("history arriving while a turn is in flight", () => {
  it("does not let an old message of the same text take the turn's place", () => {
    vi.useFakeTimers();
    vi.setSystemTime(SERVER_NOW);

    const runtime = mountRuntime();
    act(() => {
      runtime.current.mergeMessages([
        {
          id: "srv-recent",
          role: "assistant",
          kind: "TEXT",
          text: "Anything else?",
          created_at: SERVER_NOW.toISOString(),
          conversation_id: CONVERSATION_ID,
        } as ConversationMessage,
      ]);
    });
    act(() => {
      runtime.current.appendOptimisticUserMessage("yes", { conversationId: CONVERSATION_ID });
    });

    const lastYear = new Date(SERVER_NOW.getTime() - 365 * 24 * 60 * MINUTE).toISOString();
    act(() => {
      runtime.current.mergeMessages([
        { ...serverEcho("srv-ancient", "yes"), created_at: lastYear } as ConversationMessage,
      ]);
    });

    expect(runtime.userMessages("yes")).toHaveLength(2);
    expect(runtime.userMessages("yes").map((message) => message.id))
      .toEqual(["srv-ancient", expect.stringMatching(/^optimistic-user-/)]);

    // ...and the real echo still has a provisional turn waiting to claim.
    act(() => {
      runtime.current.mergeMessages([serverEcho("srv-1", "yes")]);
    });
    expect(runtime.userMessages("yes").map((message) => message.id))
      .toEqual(["srv-ancient", "srv-1"]);
  });
});
