import { describe, expect, it } from "vitest";
import {
  extractAgentFinalOutput,
  extractJsonObject,
  latestAssistantText,
  unwrapFinalResultPayload,
} from "../core/agent/output.js";

describe("extractJsonObject", () => {
  it("parses fenced JSON", () => {
    expect(extractJsonObject('here:\n```json\n{"a":1}\n```')).toEqual({ a: 1 });
  });
  it("parses a bare object embedded in text", () => {
    expect(extractJsonObject('result {"a":1} done')).toEqual({ a: 1 });
  });
  it("returns null for non-JSON", () => {
    expect(extractJsonObject("just words")).toBeNull();
  });
});

describe("unwrapFinalResultPayload", () => {
  it("unwraps structured_output", () => {
    expect(unwrapFinalResultPayload({ structured_output: { label: "x" } })).toEqual({ label: "x" });
  });
  it("strips operational keys", () => {
    expect(
      unwrapFinalResultPayload({ success: true, tool_name: "final_result", label: "x" }),
    ).toEqual({ label: "x" });
  });
});

describe("extractAgentFinalOutput", () => {
  it("prefers metadata.is_final_answer + structured_output", () => {
    const out = extractAgentFinalOutput([
      { role: "assistant", kind: "text", text: "{}", metadata: { is_final_answer: true, structured_output: { label: "spam" } } },
    ]);
    expect(out).toEqual({ label: "spam" });
  });

  it("reads a final_result tool invocation", () => {
    const out = extractAgentFinalOutput([
      {
        role: "assistant",
        parts: [{ type: "tool", toolInvocation: { toolName: "final_result", result: { score: 1 } } }],
      },
    ]);
    expect(out).toEqual({ score: 1 });
  });

  it("reads a raw final_answer tool message", () => {
    const out = extractAgentFinalOutput([
      { role: "tool", kind: "tool_return", tool_name: "final_answer", tool_result: { ok: true } },
    ]);
    expect(out).toEqual({ ok: true });
  });

  it("falls back to JSON in assistant text only when enabled", () => {
    const messages = [{ role: "assistant", kind: "text", text: '{"answer":42}' }];
    expect(extractAgentFinalOutput(messages)).toBeNull();
    expect(extractAgentFinalOutput(messages, { parseTextFallback: true })).toEqual({ answer: 42 });
  });

  // The backend stamps `is_final_answer` on every assistant text message, but
  // only writes `structured_output` (or a final-result tool call) when the
  // agent declared an output type. That payload — not the flag — is what marks
  // a run as structured, so these two cases must not be confused.
  it("ignores is_final_answer without structured_output", () => {
    const messages = [
      {
        role: "assistant",
        kind: "text",
        text: 'Here is the shape:\n```json\n{"name":"x"}\n```',
        metadata: { is_final_answer: true },
      },
    ];
    expect(extractAgentFinalOutput(messages)).toBeNull();
    expect(extractAgentFinalOutput(messages, { parseTextFallback: true })).toEqual({ name: "x" });
  });

  it("still reads a structured agent's answer out of a plain chat transcript", () => {
    const messages = [
      { role: "user", kind: "text", text: "triage this", created_at: "2026-07-31T00:00:00.000Z" },
      {
        role: "assistant",
        kind: "text",
        text: "Looks like spam.",
        metadata: {
          is_final_answer: true,
          structured_output: { label: "spam", confidence: 0.9 },
          tool_name: "final_result",
        },
        created_at: "2026-07-31T00:00:01.000Z",
      },
    ];
    expect(extractAgentFinalOutput(messages)).toEqual({ label: "spam", confidence: 0.9 });
  });
});

describe("latestAssistantText", () => {
  it("returns the latest non-user text, skipping tool messages", () => {
    const text = latestAssistantText([
      { role: "user", kind: "text", text: "hi", created_at: "2026-06-18T00:00:00.000Z" },
      { role: "assistant", kind: "tool_call", text: "calling", created_at: "2026-06-18T00:00:01.000Z" },
      { role: "assistant", kind: "text", text: "the answer", created_at: "2026-06-18T00:00:02.000Z" },
    ]);
    expect(text).toBe("the answer");
  });
});

describe("extractJsonObject on adversarial text", () => {
  it("does not degrade on a long run of spaces after an unclosed fence", () => {
    // The fence marker's `\s*` and the lazy capture that followed could both
    // claim these spaces, so rejecting this text cost quadratic time.
    const hostile = "```json" + " ".repeat(60_000) + "{";
    const started = Date.now();

    expect(extractJsonObject(hostile)).toBeNull();
    expect(Date.now() - started).toBeLessThan(1_000);
  });
});
