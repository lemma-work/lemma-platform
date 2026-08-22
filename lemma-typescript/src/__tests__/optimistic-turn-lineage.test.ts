import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";
import type { ConversationMessage } from "../types.js";
import {
  useAssistantRuntime,
  type UseAssistantRuntimeResult,
} from "../react/useAssistantRuntime.js";

// Consumers key a turn by the provisional message it started as, so the swap to
// the server's echo does not remount it. `optimistic_id` is how the echo says
// which turn it belongs to — and it has to survive every later write to that
// message, not just the write that set it.

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const roots: Root[] = [];

afterEach(() => {
  act(() => {
    roots.splice(0).forEach((root) => root.unmount());
  });
});

const CONVERSATION_ID = "conv-1";

// Stamped from the clock, like the server's would be: the store pairs an echo
// with its provisional message inside a match window, so a fixture frozen in
// the past would never pair at all and the test would pass for the wrong reason.
function userMessage(id: string, text: string): ConversationMessage {
  return {
    id,
    role: "user",
    kind: "TEXT",
    text,
    created_at: new Date().toISOString(),
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
    lineageOf(id: string) {
      const found = this.current.runtimeMessages.find((entry) => entry.id === id);
      return (found as { optimistic_id?: string } | undefined)?.optimistic_id ?? null;
    },
  };
}

describe("the link from an echo back to its provisional turn", () => {
  it("is recorded when the echo replaces the provisional message", () => {
    const runtime = mountRuntime();
    let provisionalId = "";
    act(() => {
      provisionalId = runtime.current.appendOptimisticUserMessage("hey", {
        conversationId: CONVERSATION_ID,
      }).id;
    });
    act(() => {
      runtime.current.mergeMessages([userMessage("srv-1", "hey")]);
    });

    expect(runtime.lineageOf("srv-1")).toBe(provisionalId);
  });

  it("survives a later write to the same message", () => {
    const runtime = mountRuntime();
    let provisionalId = "";
    act(() => {
      provisionalId = runtime.current.appendOptimisticUserMessage("hey", {
        conversationId: CONVERSATION_ID,
      }).id;
    });
    act(() => {
      runtime.current.mergeMessages([userMessage("srv-1", "hey")]);
    });

    // What the session does when the run finishes: it mirrors its own view of
    // the transcript, which never had the link, over the top of the store's. A
    // wholesale overwrite here dropped the lineage — and the turn, keyed by it,
    // remounted the moment the agent's answer landed.
    act(() => {
      runtime.current.mergeMessages([
        userMessage("srv-1", "hey"),
        {
          id: "srv-2",
          role: "assistant",
          kind: "TEXT",
          text: "Hey! What are we working on?",
          created_at: new Date().toISOString(),
          conversation_id: CONVERSATION_ID,
        } as ConversationMessage,
      ]);
    });

    expect(runtime.lineageOf("srv-1")).toBe(provisionalId);
  });
});
