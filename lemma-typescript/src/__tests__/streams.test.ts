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
  const events: SseRawEvent[] = [];
  for await (const event of readSSE(stream)) {
    events.push(event);
  }
  return events;
}

describe("readSSE", () => {
  it("discards an event that is not terminated by a blank line", async () => {
    const input = new TextEncoder().encode(
      'event: message\ndata: {"ok":true}',
    );

    const events = await collect(readable([input]));

    expect(events).toEqual([]);
  });

  it("treats fields without colons as fields with empty values", async () => {
    const input = new TextEncoder().encode("data: first\ndata\n\n");

    const events = await collect(readable([input]));

    expect(events).toHaveLength(1);
    expect(events[0].data).toBe("first\n");
  });

  it("does not dispatch an event with an empty data buffer", async () => {
    const input = new TextEncoder().encode("data:\n\n");

    const events = await collect(readable([input]));

    expect(events).toEqual([]);
  });

  it("dispatches events separated by CR-only line endings", async () => {
    const input = new TextEncoder().encode("event: update\rdata: ready\r\r");

    const events = await collect(readable([input]));

    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({
      event: "update",
      data: "ready",
    });
  });

  it("handles a CRLF pair split across chunks", async () => {
    const input = new TextEncoder().encode("data: split\r\n\r\n");
    const splitAt = input.indexOf(13) + 1;

    const events = await collect(
      readable([input.slice(0, splitAt), input.slice(splitAt)]),
    );

    expect(events).toHaveLength(1);
    expect(events[0].data).toBe("split");
  });

  it("removes only one optional space after an SSE field colon", async () => {
    const input = new TextEncoder().encode(
      "event:  custom\ndata:   indented  \n\n",
    );

    const events = await collect(readable([input]));

    expect(events).toHaveLength(1);
    expect(events[0].event).toBe(" custom");
    expect(events[0].data).toBe("  indented  ");
  });

  it("treats an empty event field as the default message event", async () => {
    const input = new TextEncoder().encode("event: custom\nevent:\ndata: ready\n\n");

    const events = await collect(readable([input]));

    expect(events).toHaveLength(1);
    expect(events[0].event).toBeUndefined();
    expect(events[0].data).toBe("ready");
  });

  it("ignores event fields containing a NUL character", async () => {
    const input = new TextEncoder().encode(
      "event: first\nevent: invalid\u0000name\ndata: ready\n\n",
    );

    const events = await collect(readable([input]));

    expect(events).toHaveLength(1);
    expect(events[0].event).toBe("first");
  });

  it("decodes a UTF-8 code point split across chunks", async () => {
    const input = new TextEncoder().encode("data: café\n\n");
    const splitAt = input.indexOf(0xc3) + 1;

    const events = await collect(
      readable([input.slice(0, splitAt), input.slice(splitAt)]),
    );

    expect(events).toHaveLength(1);
    expect(events[0].data).toBe("café");
    expect(parseSSEJson(events[0])).toBeNull();
  });
});
