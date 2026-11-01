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
    const raw = `${eventName ? `event: ${eventName}\n` : ""}${dataLines
      .map((line) => `data: ${line}`)
      .join("\n")}`;

    const next: SseRawEvent = {
      event: eventName,
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
    const trimmed = line.endsWith("\r") ? line.slice(0, -1) : line;

    if (trimmed === "") {
      return flush();
    }

    if (trimmed.startsWith("event:")) {
      eventName = fieldValue(trimmed, "event");
      return null;
    }

    if (trimmed.startsWith("data:")) {
      dataLines.push(fieldValue(trimmed, "data"));
    }

    return null;
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    for (const line of lines) {
      const event = processLine(line);
      if (event) {
        yield event;
      }
    }
  }

  // TextDecoder may still hold a partial UTF-8 code point from the final chunk.
  buffer += decoder.decode();

  // Process a final line that was not terminated by a line feed.
  if (buffer !== "") {
    const event = processLine(buffer);
    if (event) {
      yield event;
    }
  }

  const event = flush();
  if (event) {
    yield event;
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
