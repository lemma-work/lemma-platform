import { describe, expect, it } from "vitest";

import { parseSSEJson, readSSE, type SseRawEvent } from "../streams.js";

function readable(chunks: Uint8Array[]): ReadableStream<Uint8Array> {
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(chunk);
      }
      controller.close();
    },
  });
}

async function collect(
  stream: ReadableStream<Uint8Array>,
): Promise<SseRawEvent[]> {
  const events = [];
  for await (const event of readSSE(stream)) {
    events.push(event);
  }
  return events;
}

describe("readSSE", () => {
  it("dispatches a final event whose last line has no newline", async () => {
    const input = new TextEncoder().encode(
      'event: message\ndata: {"ok":true}',
    );

    const events = await collect(readable([input]));

    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({
      event: "message",
      data: '{"ok":true}',
    });
    expect(parseSSEJson<{ ok: boolean }>(events[0])).toEqual({ ok: true });
  });

  it("removes only one optional space after an SSE field colon", async () => {\n    const input = new TextEncoder().encode(\n      "event:  custom\\ndata:   indented  \\n\\n",\n    );\n\n    const events = await collect(readable([input]));\n\n    expect(events).toHaveLength(1);\n    expect(events[0].event).toBe(" custom");\n    expect(events[0].data).toBe("  indented  ");\n  });\n\n  it("flushes a UTF-8 code point split at the end of the stream", async () => {
    const input = new TextEncoder().encode("data: café");
    const splitAt = input.length - 1;

    const events = await collect(
      readable([input.slice(0, splitAt), input.slice(splitAt)]),
    );

    expect(events).toHaveLength(1);
    expect(events[0].data).toBe("café");
  });
});
