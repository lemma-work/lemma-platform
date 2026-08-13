import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";
import type { ConversationMessage } from "../types.js";
import {
  useAssistantRuntime,
  type UseAssistantRuntimeResult,
} from "../react/useAssistantRuntime.js";

// The store used to prune to the open conversation on every switch, so going
// back to a conversation you had just been reading meant a blank frame, a
// skeleton, and a refetch of messages that had been in memory a moment earlier.
// These cases pin the retention window that replaced it.
//
// See docs/design/conversation-messages.md.

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const roots: Root[] = [];

afterEach(() => {
  act(() => {
    roots.splice(0).forEach((root) => root.unmount());
  });
});

function message(id: string, conversationId: string): ConversationMessage {
  return {
    id,
    role: "user",
    kind: "TEXT",
    text: id,
    created_at: new Date(Date.UTC(2026, 0, 1)).toISOString(),
    conversation_id: conversationId,
  } as ConversationMessage;
}

/** Mount the runtime and return a handle that can re-render with a new conversation. */
function mountRuntime(options?: { retainConversations?: number }) {
  let latest: UseAssistantRuntimeResult | null = null;
  const container = document.createElement("div");
  const root = createRoot(container);
  roots.push(root);

  function Probe({ conversationId }: { conversationId: string | null }) {
    latest = useAssistantRuntime({
      conversationId,
      retainConversations: options?.retainConversations,
    });
    return null;
  }

  const render = (conversationId: string | null) => {
    act(() => {
      root.render(createElement(Probe, { conversationId }));
    });
  };

  return {
    render,
    get current(): UseAssistantRuntimeResult {
      if (!latest) throw new Error("Runtime is not mounted.");
      return latest;
    },
    idsFor(conversationId: string) {
      return this.current.runtimeMessages
        .filter((entry) => (entry as { conversation_id?: string }).conversation_id === conversationId)
        .map((entry) => entry.id);
    },
  };
}

describe("useAssistantRuntime retention", () => {
  it("keeps a transcript when you switch away and back", () => {
    const runtime = mountRuntime();
    runtime.render("c1");
    act(() => {
      runtime.current.replaceLoadedMessages([message("m1", "c1"), message("m2", "c1")]);
    });

    runtime.render("c2");
    act(() => {
      runtime.current.replaceLoadedMessages([message("m3", "c2")]);
    });

    // The point of the whole change: c1 is still resident while c2 is open.
    expect(runtime.current.hasConversationMessages("c1")).toBe(true);
    expect(runtime.idsFor("c1")).toEqual(["m1", "m2"]);

    runtime.render("c1");
    expect(runtime.idsFor("c1")).toEqual(["m1", "m2"]);
  });

  it("loading one conversation does not evict the others", () => {
    const runtime = mountRuntime();
    runtime.render("c1");
    act(() => {
      runtime.current.replaceLoadedMessages([message("m1", "c1")]);
    });

    runtime.render("c2");
    act(() => {
      runtime.current.replaceLoadedMessages([message("m2", "c2")]);
    });

    expect(runtime.idsFor("c1")).toEqual(["m1"]);
    expect(runtime.idsFor("c2")).toEqual(["m2"]);
  });

  it("evicts the least recently opened past the retention window", () => {
    const runtime = mountRuntime({ retainConversations: 2 });

    for (const id of ["c1", "c2"]) {
      runtime.render(id);
      act(() => {
        runtime.current.replaceLoadedMessages([message(`m-${id}`, id)]);
      });
    }

    runtime.render("c3");
    act(() => {
      runtime.current.replaceLoadedMessages([message("m-c3", "c3")]);
    });

    // c1 is the oldest of three in a window of two.
    expect(runtime.current.hasConversationMessages("c1")).toBe(false);
    expect(runtime.current.hasConversationMessages("c2")).toBe(true);
    expect(runtime.current.hasConversationMessages("c3")).toBe(true);
  });

  it("re-opening refreshes recency rather than aging out", () => {
    const runtime = mountRuntime({ retainConversations: 2 });

    for (const id of ["c1", "c2"]) {
      runtime.render(id);
      act(() => {
        runtime.current.replaceLoadedMessages([message(`m-${id}`, id)]);
      });
    }

    // Touch c1 again, so c2 becomes the oldest.
    runtime.render("c1");
    runtime.render("c3");
    act(() => {
      runtime.current.replaceLoadedMessages([message("m-c3", "c3")]);
    });

    expect(runtime.current.hasConversationMessages("c1")).toBe(true);
    expect(runtime.current.hasConversationMessages("c2")).toBe(false);
  });

  it("clear() drops everything, including retained transcripts", () => {
    const runtime = mountRuntime();
    runtime.render("c1");
    act(() => {
      runtime.current.replaceLoadedMessages([message("m1", "c1")]);
    });
    runtime.render("c2");

    act(() => {
      runtime.current.clear();
    });

    expect(runtime.current.runtimeMessages).toEqual([]);
    expect(runtime.current.hasConversationMessages("c1")).toBe(false);
  });

  it("closing the open conversation keeps the store warm", () => {
    const runtime = mountRuntime();
    runtime.render("c1");
    act(() => {
      runtime.current.replaceLoadedMessages([message("m1", "c1")]);
    });

    runtime.render(null);

    expect(runtime.current.hasConversationMessages("c1")).toBe(true);
  });
});

// The session mirrors its messages into the store through an effect, so for one
// commit a message that has already arrived is not in the store yet. That gap is
// what made the assistant's answer blink out as a turn ended. The store now
// merges the session's view at derive time.
describe("useAssistantRuntime session handoff", () => {
  function mountWithSession() {
    let latest: UseAssistantRuntimeResult | null = null;
    const container = document.createElement("div");
    const root = createRoot(container);
    roots.push(root);

    function Probe({ sessionMessages }: { sessionMessages: ConversationMessage[] }) {
      latest = useAssistantRuntime({
        conversationId: "c1",
        sessionConversationId: "c1",
        sessionMessages,
      });
      return null;
    }

    return {
      render(sessionMessages: ConversationMessage[]) {
        act(() => {
          root.render(createElement(Probe, { sessionMessages }));
        });
      },
      get ids(): string[] {
        if (!latest) throw new Error("Runtime is not mounted.");
        return latest.runtimeMessages.map((entry) => entry.id);
      },
    };
  }

  it("surfaces a session message the mirror effect skipped", () => {
    const runtime = mountWithSession();
    const a = message("a", "c1");
    runtime.render([a]);
    expect(runtime.ids).toContain("a");

    // The mirror effect early-returns when the *last* session message id is
    // unchanged, so an insert before it is never mirrored into state.
    const b = message("b", "c1");
    runtime.render([b, a]);

    expect(runtime.ids).toContain("b");
    expect(runtime.ids).toContain("a");
  });

  it("returns the store untouched once the session view is already mirrored", () => {
    const runtime = mountWithSession();
    const a = message("a", "c1");
    runtime.render([a]);
    const first = runtime.ids;
    runtime.render([a]);
    expect(runtime.ids).toEqual(first);
  });
});
