/**
 * Who said a message must survive every hop it takes.
 *
 * A message reaches the transcript by two different routes — the REST read and
 * the realtime frame — and each is normalised by its own hand-written mapping
 * that names every field it copies. Identity was added to the schema and left
 * out of those mappings four separate times: the API response, the SDK's
 * renderable type, the controller's mapping, and the stream normaliser. Every
 * time, the symptom was the same: replies from a named agent showed the default
 * agent's name, and only corrected themselves on a refetch.
 *
 * These pin both routes, so the next field added to the schema fails here
 * rather than in a transcript.
 */
import { describe, expect, it } from "vitest";

import { parseAssistantStreamEvent } from "../assistant-events.js";

/** Every field that answers "who is this from". */
const IDENTITY_FIELDS = ["sender_user_id", "agent_id", "agent_run_id"] as const;

function frame(overrides: Record<string, unknown> = {}) {
  return {
    type: "message",
    data: {
      id: "msg_1",
      role: "assistant",
      kind: "TEXT",
      text: "hello",
      created_at: new Date().toISOString(),
      conversation_id: "conv_1",
      sequence: 3,
      agent_run_id: "run_1",
      sender_user_id: "user_1",
      agent_id: "agent_1",
      ...overrides,
    },
  };
}

describe("a streamed message keeps its identity", () => {
  it("carries every identity field through the stream normaliser", () => {
    const parsed = parseAssistantStreamEvent(frame());
    const message = parsed?.message;

    expect(message).toBeTruthy();
    for (const field of IDENTITY_FIELDS) {
      expect(
        (message as Record<string, unknown>)[field],
        `${field} was dropped in transit, so the transcript cannot attribute this message`,
      ).toBeTruthy();
    }
  });

  it("reports null rather than undefined when a field is genuinely absent", () => {
    // An agent's own message has no sender; a person's has no agent. Both are
    // real states, and neither should look like a field that went missing.
    const parsed = parseAssistantStreamEvent(
      frame({ sender_user_id: undefined, agent_id: undefined }),
    );

    expect(parsed?.message?.sender_user_id).toBeNull();
    expect(parsed?.message?.agent_id).toBeNull();
  });
});
