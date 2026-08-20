// Unit tests for the turn adapter (lib/assistant/turns.ts).
//
// The adapter is the redesign's load-bearing logic: it decides what becomes a
// bubble, what becomes the document card, what folds into the status pill's
// trace, and which files earn artifact cards. Rows come from the SDK's real
// buildDisplayMessageRows, so these tests also pin how the two pipelines
// compose — including the fold of intermediate narration into trace notes.

import { describe, expect, it } from "vitest";
import { buildDisplayMessageRows, type AssistantRenderableMessage } from "lemma-sdk";
import { answerIsDocument, buildChatTurns, chatTurnFingerprint, completedTurnStatusLabel } from "../turns";

const T0 = new Date("2026-07-30T16:40:00Z").getTime();

function at(offsetSeconds: number): Date {
  return new Date(T0 + offsetSeconds * 1000);
}

function userMessage(text: string, offsetSeconds = 0): AssistantRenderableMessage {
  return {
    id: `user-${offsetSeconds}`,
    role: "user",
    kind: "TEXT",
    content: text,
    createdAt: at(offsetSeconds),
  };
}

function assistantText(
  text: string,
  offsetSeconds: number,
  metadata?: Record<string, unknown>,
): AssistantRenderableMessage {
  return {
    id: `asst-${offsetSeconds}`,
    role: "assistant",
    kind: "TEXT",
    content: text,
    createdAt: at(offsetSeconds),
    metadata: metadata ?? { is_final_answer: true },
  };
}

function toolCall(
  toolName: string,
  toolCallId: string,
  offsetSeconds: number,
  args: Record<string, unknown> = {},
  result?: Record<string, unknown>,
): AssistantRenderableMessage {
  return {
    id: `tool-${toolCallId}`,
    role: "assistant",
    kind: "TOOL_CALL",
    content: "",
    tool_name: toolName,
    tool_call_id: toolCallId,
    tool_args: args,
    tool_result: result,
    createdAt: at(offsetSeconds),
    toolInvocations: [{
      toolCallId,
      toolName,
      args,
      state: result ? "result" : "call",
      result,
    }],
    parts: [{
      id: `part-${toolCallId}`,
      type: "tool",
      toolInvocation: {
        toolCallId,
        toolName,
        args,
        state: result ? "result" : "call",
        result,
      },
    }],
  };
}

function thinking(text: string, offsetSeconds: number): AssistantRenderableMessage {
  return {
    id: `think-${offsetSeconds}`,
    role: "assistant",
    kind: "THINKING",
    content: text,
    createdAt: at(offsetSeconds),
    parts: [{ id: `think-part-${offsetSeconds}`, type: "reasoning", text, state: "done" }],
  };
}

function turnsFor(messages: AssistantRenderableMessage[], isRunActive = false) {
  return buildChatTurns({
    rows: buildDisplayMessageRows(messages),
    messages,
    isRunActive,
    podId: "pod-1",
    conversationId: "conv-1",
  });
}

describe("buildChatTurns", () => {
  it("a plain question and reply is one turn, one answer, no trace", () => {
    const turns = turnsFor([
      userMessage("can you create videos using hyperframes?"),
      assistantText("Yeah — HyperFrames is HTML in, MP4 out.", 10),
    ]);

    expect(turns).toHaveLength(1);
    expect(turns[0].userMessage?.content).toContain("hyperframes");
    expect(turns[0].items).toEqual([
      expect.objectContaining({ kind: "text", answer: true, text: "Yeah — HyperFrames is HTML in, MP4 out." }),
    ]);
    expect(turns[0].trace).toHaveLength(0);
    expect(completedTurnStatusLabel(turns[0])).toBeNull();
  });

  it("narration stays speech and the work folds into the trace", () => {
    const turns = turnsFor([
      userMessage("can you create a pdf and a presentation?"),
      assistantText("On it — pulling together sourced content first.", 5, { is_intermediate_assistant_message: true }),
      toolCall("search_query", "call-1", 20, { query: "hermes agent" }, { success: true }),
      toolCall("exec_command", "call-2", 40, { cmd: "python build.py" }, { success: true }),
      assistantText("Done — both files are in your pod.", 60),
    ]);

    expect(turns).toHaveLength(1);
    const turn = turns[0];

    const texts = turn.items.filter((item) => item.kind === "text");
    // Narration comes first, never answer-weight; the final answer closes.
    expect(texts[0]).toMatchObject({ text: "On it — pulling together sourced content first.", answer: false });
    expect(texts[texts.length - 1]).toMatchObject({ text: "Done — both files are in your pod.", answer: true });

    expect(turn.trace.map((entry) => entry.kind)).toEqual(["tool", "tool"]);
    expect(turn.toolCount).toBe(2);
  });

  it("narration survives a finished run as bubbles (intermediate TEXT never folds)", () => {
    // The SDK's fold only converts THINKING messages; mid-run speech is TEXT
    // with is_intermediate_assistant_message and stays a row. The adapter must
    // read that flag and keep the beat as a narration bubble.
    const turns = turnsFor([
      userMessage("make the thing"),
      assistantText("Setting up the build tooling now.", 5, { is_intermediate_assistant_message: true }),
      toolCall("exec_command", "call-1", 10, { cmd: "make" }, { success: true }),
      assistantText("Built.", 20),
    ]);

    const texts = turns[0].items.filter((item) => item.kind === "text");
    expect(texts.map((item) => item.kind === "text" && item.text)).toEqual([
      "Setting up the build tooling now.",
      "Built.",
    ]);
    expect(texts[0]).toMatchObject({ answer: false });
    expect(turns[0].trace.filter((entry) => entry.kind === "thinking")).toHaveLength(0);
  });

  it("folded thinking (traceNote) is a trace entry, never a bubble — and never duplicated", () => {
    // With a final answer present, the SDK converts the THINKING message to a
    // traceNote reasoning part, and can leave the original part beside it.
    const turns = turnsFor([
      userMessage("think about this"),
      thinking("Let me reason through the options…", 5),
      toolCall("exec_command", "call-1", 10, { cmd: "true" }, { success: true }),
      assistantText("The answer is 4.", 30),
    ]);

    const thoughts = turns[0].trace.filter((entry) => entry.kind === "thinking");
    expect(thoughts).toHaveLength(1);
    expect(thoughts[0]).toMatchObject({ text: "Let me reason through the options…" });
    const texts = turns[0].items.filter((item) => item.kind === "text");
    expect(texts).toHaveLength(1);
    expect(texts[0]).toMatchObject({ text: "The answer is 4.", answer: true });
  });

  it("genuine thinking goes to the trace, never to a bubble", () => {
    const turns = turnsFor([
      userMessage("think about this"),
      thinking("Let me reason through the options…", 5),
      assistantText("The answer is 4.", 30),
    ]);

    expect(turns[0].trace).toEqual([
      expect.objectContaining({ kind: "thinking", text: "Let me reason through the options…" }),
    ]);
    expect(turns[0].items.filter((item) => item.kind === "text")).toHaveLength(1);
  });

  it("counts steps and durations for the status pill — recovered failures stay out of it", () => {
    const turns = turnsFor([
      userMessage("do the work"),
      toolCall("exec_command", "call-1", 10, { cmd: "one" }, { success: true }),
      toolCall("exec_command", "call-2", 20, { cmd: "two" }, { success: false, error: "boom" }),
      toolCall("exec_command", "call-3", 30, { cmd: "three" }, { success: true }),
      assistantText("Done.", 554), // 9m 4s after the first step
    ]);

    // First step at +10s, answer at +554s. The retried failure keeps its row
    // in the trace but never reaches the pill.
    expect(turns[0].failedCount).toBe(1);
    expect(completedTurnStatusLabel(turns[0])).toBe("Worked for 9m 4s · 3 steps");
  });

  it("labels a thought-only turn as thinking", () => {
    const turns = turnsFor([
      userMessage("muse"),
      thinking("Hmm.", 10),
      assistantText("Ok.", 25),
    ]);
    expect(completedTurnStatusLabel(turns[0])).toBe("Thought for 15s");
  });

  it("deliverable file writes become artifacts; build scripts stay in the trace", () => {
    const turns = turnsFor([
      userMessage("make the brief"),
      toolCall("create_file", "call-1", 10, { path: "/me/build.py" }, { success: true, path: "/me/build.py", size_bytes: 1200 }),
      toolCall("pod_write_file", "call-2", 20, { path: "/me/hermes/Team_Brief.pdf" }, { success: true, path: "/me/hermes/Team_Brief.pdf", size_bytes: 182_000 }),
      assistantText("Done — the brief is in your pod files.", 30),
    ]);

    expect(turns[0].artifacts).toHaveLength(1);
    expect(turns[0].artifacts[0]).toMatchObject({
      path: "/me/hermes/Team_Brief.pdf",
      fileName: "Team_Brief.pdf",
      name: "Team Brief",
      ext: "PDF",
      kind: "file",
      sizeBytes: 182_000,
    });
    expect(turns[0].artifacts[0].href).toContain("/pod/pod-1/files");
    // Both writes are still work rows in the trace.
    expect(turns[0].toolCount).toBe(2);
  });

  it("a presented file and a written file at the same path dedupe to one card", () => {
    const turns = turnsFor([
      userMessage("make it and show it"),
      toolCall("pod_write_file", "call-1", 10, { path: "/me/intro.mp4" }, { success: true, path: "/me/intro.mp4", size_bytes: 4_200_000 }),
      toolCall("display_resource", "call-2", 20, { type: "FILE", path: "/me/intro.mp4" }, { success: true }),
      assistantText("Here it is.", 30),
    ]);

    expect(turns[0].artifacts).toHaveLength(1);
    expect(turns[0].artifacts[0]).toMatchObject({ path: "/me/intro.mp4", kind: "video", sizeBytes: 4_200_000 });
    // display_resource itself never lands in the trace.
    expect(turns[0].trace.filter((entry) => entry.kind === "tool"
      && entry.invocation.toolName === "display_resource")).toHaveLength(0);
  });

  it("non-file display resources stay resource cards, not artifacts", () => {
    const turns = turnsFor([
      userMessage("show the table"),
      toolCall("display_resource", "call-1", 10, { type: "TABLE", name: "orders" }, { success: true }),
      assistantText("Here are the orders.", 20),
    ]);

    expect(turns[0].artifacts).toHaveLength(0);
    expect(turns[0].resources).toHaveLength(1);
    expect(turns[0].resources[0].request.type).toBe("TABLE");
  });

  it("ask_user becomes an in-chat interaction card, pending until answered", () => {
    const pending = turnsFor([
      userMessage("deploy it"),
      toolCall("ask_user", "call-1", 10, { questions: [{ header: "Env", question: "Where?", options: [{ label: "Staging" }] }] }),
    ]);
    expect(pending[0].items.some((item) => item.kind === "interaction")).toBe(true);
    expect(pending[0].hasPendingInteraction).toBe(true);
    expect(pending[0].trace).toHaveLength(0);

    const answered = turnsFor([
      userMessage("deploy it"),
      toolCall("ask_user", "call-1", 10, { questions: [] }, { answers: { Env: "Staging" } }),
      assistantText("Deployed to staging.", 20),
    ]);
    expect(answered[0].hasPendingInteraction).toBe(false);
  });

  it("only the tail turn is live, and a just-sent turn is live with no output yet", () => {
    const turns = turnsFor([
      userMessage("first", 0),
      assistantText("First answer.", 10),
      userMessage("second", 20),
    ], true);

    expect(turns).toHaveLength(2);
    expect(turns[0].isLive).toBe(false);
    expect(turns[1].isLive).toBe(true);
  });

  it("only the flagged final answer is doc-eligible — long mid-turn narration stays a bubble", () => {
    // The dark-screenshot bug: a long unflagged beat mid-run must not become a
    // document card just because it is long.
    const longNarration = `Let me check what is available. ${"Checking connectors takes a while. ".repeat(40)}`;
    const turns = turnsFor([
      userMessage("notify a member"),
      assistantText(longNarration, 5, {}), // unflagged mid-run beat
      toolCall("exec_command", "call-1", 10, { cmd: "lemma connectors" }, { success: true }),
      assistantText("## Connectors\n\n- Slack\n- Telegram\n- Gmail", 30),
    ]);

    const texts = turns[0].items.filter((item) => item.kind === "text");
    expect(texts[0]).toMatchObject({ answer: true, final: false, documentEligible: false });
    expect(texts[1]).toMatchObject({ answer: true, final: true, documentEligible: true });
  });

  it("unflagged history falls back to the closing run of answer text", () => {
    const turns = turnsFor([
      userMessage("tell me a story"),
      assistantText(`A very long tale. ${"It rambles on and on. ".repeat(40)}`, 5, {}),
    ]);
    expect(turns[0].items[0]).toMatchObject({ final: false, documentEligible: true });
  });

  it("empty assistant messages leave no turn and no bubble", () => {
    const turns = turnsFor([
      userMessage("hi"),
      { id: "empty-1", role: "assistant", kind: "TEXT", content: "", createdAt: at(5) },
      assistantText("Hello!", 10),
    ]);
    expect(turns).toHaveLength(1);
    expect(turns[0].items.filter((item) => item.kind === "text")).toHaveLength(1);
  });

  it("notifications render as separators, not bubbles", () => {
    const turns = turnsFor([
      userMessage("hi"),
      {
        id: "note-1",
        role: "system",
        kind: "NOTIFICATION",
        content: "Nightly digest ran",
        createdAt: at(5),
      },
      assistantText("Hello!", 10),
    ]);
    expect(turns[0].items).toContainEqual(expect.objectContaining({ kind: "notice", text: "Nightly digest ran" }));
  });
});

describe("optimistic turn id continuity", () => {
  // Sending shows the transcript a provisional user message first
  // (`optimistic-user-…`); the server echo then replaces it, changing the
  // message id. If the turn id changed with it, the live turn would remount
  // and its bubble and status pill would replay their entrance animations.

  it("keeps the turn id when the server echo replaces the provisional message", () => {
    const provisional: AssistantRenderableMessage = {
      ...userMessage("hello there"),
      id: "optimistic-user-abc",
    };
    expect(turnsFor([provisional], true)[0].id).toBe("turn-optimistic-user-abc");

    const echoed: AssistantRenderableMessage = { ...userMessage("hello there"), id: "srv-1" };
    expect(turnsFor([echoed], true)[0].id).toBe("turn-optimistic-user-abc");
  });

  it("keeps the inherited id across later rebuilds", () => {
    const provisional: AssistantRenderableMessage = {
      ...userMessage("hello again"),
      id: "optimistic-user-x",
    };
    const provisionalId = turnsFor([provisional], true)[0].id;

    const echoed: AssistantRenderableMessage = { ...userMessage("hello again"), id: "srv-1" };
    const first = turnsFor([echoed], true);
    const second = turnsFor([echoed, assistantText("Working on it", 5, { is_intermediate_assistant_message: true })], true);
    expect(first[0].id).toBe(provisionalId);
    expect(second[0].id).toBe(provisionalId);
  });

  it("does not hand the inherited id to a later message with the same content", () => {
    const provisional: AssistantRenderableMessage = {
      ...userMessage("same question"),
      id: "optimistic-user-first",
    };
    const echoed: AssistantRenderableMessage = { ...userMessage("same question"), id: "srv-first" };
    const firstTurnId = turnsFor([provisional], true)[0].id;
    expect(turnsFor([echoed, assistantText("Answer.", 5)], false)[0].id).toBe(firstTurnId);

    const secondProvisional: AssistantRenderableMessage = {
      ...userMessage("same question", 300),
      id: "optimistic-user-second",
    };
    const secondEchoed: AssistantRenderableMessage = { ...userMessage("same question", 300), id: "srv-second" };
    const both = turnsFor([echoed, assistantText("Answer.", 5), secondProvisional], true);
    expect(both[1].id).toBe("turn-optimistic-user-second");

    const afterSecondEcho = turnsFor([echoed, assistantText("Answer.", 5), secondEchoed], true);
    expect(afterSecondEcho[1].id).toBe("turn-optimistic-user-second");
  });

  it("keeps two identical provisional messages on distinct turns", () => {
    const optA: AssistantRenderableMessage = { ...userMessage("yes"), id: "optimistic-user-a" };
    const optB: AssistantRenderableMessage = { ...userMessage("yes", 5), id: "optimistic-user-b" };
    expect(turnsFor([optA, optB], true).map((turn) => turn.id))
      .toEqual(["turn-optimistic-user-a", "turn-optimistic-user-b"]);

    const realA: AssistantRenderableMessage = { ...userMessage("yes"), id: "srv-a" };
    expect(turnsFor([realA, optB], true).map((turn) => turn.id))
      .toEqual(["turn-optimistic-user-a", "turn-optimistic-user-b"]);
  });
});

describe("answerIsDocument", () => {
  it("short prose is a bubble", () => {
    expect(answerIsDocument("Done — both files are in your pod.")).toBe(false);
    expect(answerIsDocument("The answer is 4.\n\nWant the working?")).toBe(false);
  });

  it("headings, tables, and real lists are documents", () => {
    expect(answerIsDocument("## Summary\n\nIt worked.")).toBe(true);
    expect(answerIsDocument("| a | b |\n| - | - |\n| 1 | 2 |")).toBe(true);
    expect(answerIsDocument("Here:\n- one thing\n- another thing\n- a third thing")).toBe(true);
    expect(answerIsDocument(`Long answer. ${"lorem ipsum ".repeat(80)}`)).toBe(true);
  });
});

describe("chatTurnFingerprint", () => {
  // The fingerprint is what lets the memoized turn view skip history while one
  // turn streams: it must hold still when a rebuild changes nothing, and move
  // the moment anything render-visible changes.

  it("is stable across an identical rebuild", () => {
    const messages = [
      userMessage("do the work"),
      toolCall("exec_command", "call-1", 10, { cmd: "one" }, { success: true }),
      assistantText("Done.", 20),
    ];
    expect(chatTurnFingerprint(turnsFor(messages)[0]))
      .toBe(chatTurnFingerprint(turnsFor(messages)[0]));
  });

  it("changes when streaming text grows", () => {
    const before = turnsFor([
      userMessage("write it"),
      assistantText("Working on it", 5, { is_intermediate_assistant_message: true }),
    ], true);
    const after = turnsFor([
      userMessage("write it"),
      assistantText("Working on it — almost there", 5, { is_intermediate_assistant_message: true }),
    ], true);
    expect(chatTurnFingerprint(before[0])).not.toBe(chatTurnFingerprint(after[0]));
  });

  it("changes when a tool call settles into a result", () => {
    const before = turnsFor([
      userMessage("run it"),
      toolCall("exec_command", "call-1", 10, { cmd: "one" }),
    ], true);
    const after = turnsFor([
      userMessage("run it"),
      toolCall("exec_command", "call-1", 10, { cmd: "one" }, { success: true }),
    ], true);
    expect(chatTurnFingerprint(before[0])).not.toBe(chatTurnFingerprint(after[0]));
  });

  it("changes when the run settles (isLive flips)", () => {
    const messages = [userMessage("hi"), assistantText("Hello.", 5)];
    expect(chatTurnFingerprint(turnsFor(messages, true)[0]))
      .not.toBe(chatTurnFingerprint(turnsFor(messages, false)[0]));
  });

  it("does not change for an unrelated later turn", () => {
    const first = [userMessage("first"), assistantText("One.", 5)];
    const both = [...first, userMessage("second", 10), assistantText("Two.", 15)];
    expect(chatTurnFingerprint(turnsFor(first)[0]))
      .toBe(chatTurnFingerprint(turnsFor(both)[0]));
  });
});
