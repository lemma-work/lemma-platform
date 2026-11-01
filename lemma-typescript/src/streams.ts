export interface SseRawEvent {
  event?: string;
  data: string;
  raw: string;
}

/**
 * Async iterator over Server-Sent Event frames.
 */
export async function* readSSE(
  stream: ReadableStream<Uint8Array>,
): AsyncGenerator<SseRawEvent, void, unknown> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();

  let buffer = "";
  let eventName: string | undefined;
  let dataLines: string[] = [];

  const flush = (): SseRawEvent | null => {
    if (dataLines.length === 0) {
      eventName = undefined;
      return null;
    }

    const data = dataLines.join("\n");
    if (data.length === 0) {
      eventName = undefined;
      dataLines = [];
      return null;
    }

    const raw = `${eventName ? `event: ${eventName}\n` : ""}${dataLines
      .map((line) => `data: ${line}`)
      .join("\n")}`;

    const next: SseRawEvent = {
      // An empty event type is the default "message" event; leave the
      // optional field unset so callers do not dispatch an empty event name.
      event: eventName || undefined,
      data,
      raw,
    };

    eventName = undefined;
    dataLines = [];
    return next;
  };

  const fieldValue = (line: string, field: string): string => {
    const value = line.slice(field.length + 1);
    return value.startsWith(" ") ? value.slice(1) : value;
  };

  const processLine = (line: string): SseRawEvent | null => {
    if (line === "") {
      return flush();
    }

    if (line.startsWith(":")) {
      return null;
    }

    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    const value = colon === -1 ? "" : fieldValue(line, field);

    if (field === "event") {
      // The WHATWG algorithm ignores event fields containing U+0000.
      if (!value.includes("\0")) {
        eventName = value;
      }
    } else if (field === "data") {
      dataLines.push(value);
    }

    return null;
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    // Keep a trailing CR until the next chunk determines whether it is CRLF.
    buffer = buffer.replace(/\r\n/g, "\n").replace(/\r(?!$)/g, "\n");

    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    for (const line of lines) {
      const event = processLine(line);
      if (event) {
        yield event;
      }
    }
  }

  // Finish decoding and accept a final standalone CR as a line ending. Any
  // remaining non-terminated line/event is intentionally discarded: SSE
  // dispatch requires a terminating blank line.
  buffer += decoder.decode();
  buffer = buffer.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  const finalLines = buffer.split("\n");
  buffer = finalLines.pop() || "";

  for (const line of finalLines) {
    const event = processLine(line);
    if (event) {
      yield event;
    }
  }
}

export function parseSSEJson<T = unknown>(event: SseRawEvent): T | null {
  if (!event.data || event.data === "[DONE]") {
    return null;
  }

  try {
    return JSON.parse(event.data) as T;
  } catch {
    return null;
  }
}
