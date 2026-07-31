import { describe, expect, it } from "vitest";
import {
  parseTodoItems,
  reasoningPartLabel,
} from "@/components/lemma/assistant/assistant-format";

describe("assistant plan display", () => {
  it("uses the authoritative full write_todos result instead of partial update args", () => {
    expect(parseTodoItems(
      { todos: ["- [x] Step two"] },
      { success: true, todos: ["- [x] Step one", "- [x] Step two", "- [ ] Step three"] },
    )).toEqual([
      { state: "done", text: "Step one" },
      { state: "done", text: "Step two" },
      { state: "todo", text: "Step three" },
    ]);
  });

  it("renders a flattened XML-like snapshot as real checklist items", () => {
    expect(parseTodoItems(
      {
        todos: [
          "Research — done</item>\n"
          + "<item>Write report — in progress</item>\n"
          + "<item>Upload</item>\n</todos>",
        ],
      },
      {
        success: true,
        todos: [
          "- [ ] old malformed snapshot</td>\n<item>- [ ] old task</td>",
        ],
      },
    )).toEqual([
      { state: "done", text: "Research" },
      { state: "active", text: "Write report" },
      { state: "todo", text: "Upload" },
    ]);
  });
});

describe("assistant thought duration label", () => {
  it("shows seconds only for an explicit duration", () => {
    expect(reasoningPartLabel(false)).toBe("Thought");
    expect(reasoningPartLabel(false, 4200)).toBe("Thought for 4s");
  });
});
