import { describe, expect, it } from "vitest";
import {
  resolveStreamingThinking,
  type HeldStreamingThinking,
} from "../react/useAssistantController.js";

// A thought reaches the UI twice: as `thinking` tokens, and again as a durable
// THINKING message. The session drops the token buffer the moment that message
// upserts, but the runtime mirrors session messages through an effect, so the
// durable row is a commit behind. These cases pin the bridge that keeps exactly
// one reasoning row on screen across that window.

function held(text: string, conversationId = "conversation-1"): { current: HeldStreamingThinking | null } {
  return { current: { conversationId, text } };
}

function durableThought(text: string) {
  return { id: "thought-1", role: "assistant", kind: "THINKING", text } as never;
}

describe("streaming thinking handoff", () => {
  it("shows the streamed thought and remembers it", () => {
    const ref: { current: HeldStreamingThinking | null } = { current: null };

    const resolved = resolveStreamingThinking({
      held: ref,
      conversationId: "conversation-1",
      streamed: "Checking the schema",
      messages: [],
      isRunning: true,
    });

    expect(resolved).toBe("Checking the schema");
    expect(ref.current).toEqual({
      conversationId: "conversation-1",
      text: "Checking the schema",
    });
  });

  it("keeps the thought on screen while the durable message is still in flight", () => {
    const ref = held("Checking the schema");

    expect(resolveStreamingThinking({
      held: ref,
      conversationId: "conversation-1",
      streamed: "",
      messages: [],
      isRunning: true,
    })).toBe("Checking the schema");
  });

  it("stands down once the durable message lands", () => {
    const ref = held("Checking the schema");

    // The durable text is the buffer plus whatever the model emitted after the
    // last token flush, so the match is by prefix, not equality.
    expect(resolveStreamingThinking({
      held: ref,
      conversationId: "conversation-1",
      streamed: "",
      messages: [durableThought("Checking the schema before answering")],
      isRunning: true,
    })).toBe("");
    expect(ref.current).toBeNull();
  });

  it("stands down when the run ends without a durable message", () => {
    const ref = held("Checking the schema");

    expect(resolveStreamingThinking({
      held: ref,
      conversationId: "conversation-1",
      streamed: "",
      messages: [],
      isRunning: false,
    })).toBe("");
    expect(ref.current).toBeNull();
  });

  it("never leaks a thought into another conversation", () => {
    const ref = held("Checking the schema", "conversation-1");

    expect(resolveStreamingThinking({
      held: ref,
      conversationId: "conversation-2",
      streamed: "",
      messages: [],
      isRunning: true,
    })).toBe("");
    expect(ref.current).toBeNull();
  });
});
