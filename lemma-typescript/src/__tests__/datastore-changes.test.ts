import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  watchDatastoreChanges,
  type ChangeStreamTokenProvider,
} from "../datastore-changes.js";

// jsdom has no WebSocket, so stub a controllable fake that records instances
// and lets the test drive open/message/close.
class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  url: string;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  closeCode?: number;

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }

  close(code?: number): void {
    this.closed = true;
    this.closeCode = code;
  }
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

let originalWebSocket: unknown;

function makeAuth(): ChangeStreamTokenProvider & {
  getAccessToken: ReturnType<typeof vi.fn>;
  refreshAccessToken: ReturnType<typeof vi.fn>;
} {
  return {
    getAccessToken: vi.fn(async () => "TOKEN"),
    refreshAccessToken: vi.fn(async () => "TOKEN2"),
  };
}

beforeEach(() => {
  FakeWebSocket.instances = [];
  originalWebSocket = (globalThis as { WebSocket?: unknown }).WebSocket;
  (globalThis as { WebSocket?: unknown }).WebSocket =
    FakeWebSocket as unknown as typeof WebSocket;
  vi.spyOn(Math, "random").mockReturnValue(0); // make reconnect delay 0
});

afterEach(() => {
  (globalThis as { WebSocket?: unknown }).WebSocket = originalWebSocket;
  vi.restoreAllMocks();
});

describe("watchDatastoreChanges", () => {
  it("builds the ws url with token, table, and since; delivers frames", async () => {
    const auth = makeAuth();
    const onChange = vi.fn();
    const onReady = vi.fn();

    const handle = watchDatastoreChanges("https://api.x.test", auth, "POD", {
      table: "notes",
      since: "1-0",
      onChange,
      onReady,
    });
    await flush();

    const ws = FakeWebSocket.instances[0];
    expect(ws.url).toBe(
      "wss://api.x.test/pods/POD/datastore/changes?table=notes&since=1-0&access_token=TOKEN",
    );

    ws.onopen?.();
    ws.onmessage?.({ data: JSON.stringify({ type: "ready", since: "1-0" }) });
    expect(onReady).toHaveBeenCalledWith({ since: "1-0" });

    const frame = {
      type: "datastore.record.insert",
      table_name: "notes",
      record_id: "a",
      operation: "insert",
      payload: { body: "hi" },
      stream_id: "2-0",
    };
    ws.onmessage?.({ data: JSON.stringify(frame) });
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith(frame);

    handle.close();
  });

  it("converts http to ws (insecure) for local servers", async () => {
    const auth = makeAuth();
    const handle = watchDatastoreChanges("http://localhost:8711", auth, "POD", {
      onChange: vi.fn(),
    });
    await flush();
    expect(FakeWebSocket.instances[0].url).toBe(
      "ws://localhost:8711/pods/POD/datastore/changes?access_token=TOKEN",
    );
    handle.close();
  });

  it("refreshes the token and resumes from cursor after a 1008 close", async () => {
    const auth = makeAuth();
    const handle = watchDatastoreChanges("https://api.x.test", auth, "POD", {
      onChange: vi.fn(),
    });
    await flush();

    const first = FakeWebSocket.instances[0];
    first.onopen?.();
    first.onmessage?.({ data: JSON.stringify({ type: "ready", since: "5-0" }) });
    first.onclose?.({ code: 1008 });

    // refreshAccessToken (microtask) -> scheduleReconnect -> setTimeout(0) -> connect
    await flush();
    await flush();
    await flush();

    expect(auth.refreshAccessToken).toHaveBeenCalledTimes(1);
    expect(FakeWebSocket.instances.length).toBe(2);
    expect(FakeWebSocket.instances[1].url).toContain("since=5-0");

    handle.close();
  });

  it("keeps backing off when the server accepts and drops the socket before it is ready", async () => {
    vi.useFakeTimers();
    try {
      vi.spyOn(Math, "random").mockReturnValue(1); // delay = the full ceiling
      const handle = watchDatastoreChanges("https://api.x.test", makeAuth(), "POD", {
        onChange: vi.fn(),
      });
      await vi.advanceTimersByTimeAsync(0);

      const dropBeforeReady = () => {
        const socket = FakeWebSocket.instances[FakeWebSocket.instances.length - 1];
        socket.onopen?.();
        socket.onclose?.({ code: 1006 });
      };

      dropBeforeReady();
      await vi.advanceTimersByTimeAsync(500);
      expect(FakeWebSocket.instances.length).toBe(2);

      /* Resetting on open made this 500 ms again, every time. */
      dropBeforeReady();
      await vi.advanceTimersByTimeAsync(999);
      expect(FakeWebSocket.instances.length).toBe(2);
      await vi.advanceTimersByTimeAsync(1);
      expect(FakeWebSocket.instances.length).toBe(3);

      /* A connection that got as far as `ready` was a real one. */
      const socket = FakeWebSocket.instances[2];
      socket.onopen?.();
      socket.onmessage?.({ data: JSON.stringify({ type: "ready", since: "5-0" }) });
      socket.onclose?.({ code: 1006 });
      await vi.advanceTimersByTimeAsync(500);
      expect(FakeWebSocket.instances.length).toBe(4);

      handle.close();
    } finally {
      vi.useRealTimers();
    }
  });

  it("stops for good once the session is signed out", async () => {
    const onError = vi.fn();
    const auth = { ...makeAuth(), getState: () => ({ status: "unauthenticated" }) };
    const handle = watchDatastoreChanges("https://api.x.test", auth, "POD", {
      onChange: vi.fn(),
      onError,
    });
    await flush();

    FakeWebSocket.instances[0].onclose?.({ code: 1006 });
    await flush();
    await flush();

    expect(FakeWebSocket.instances.length).toBe(1);
    expect(handle.closed).toBe(true);
    expect(onError).toHaveBeenCalledWith(expect.objectContaining({ message: expect.stringContaining("signed out") }));
  });

  it("stops reconnecting after close()", async () => {
    const auth = makeAuth();
    const handle = watchDatastoreChanges("https://api.x.test", auth, "POD", {
      onChange: vi.fn(),
    });
    await flush();

    expect(FakeWebSocket.instances.length).toBe(1);
    handle.close();
    expect(handle.closed).toBe(true);
    expect(FakeWebSocket.instances[0].closed).toBe(true);

    // A late close event must not spawn a reconnect.
    FakeWebSocket.instances[0].onclose?.({ code: 1006 });
    await flush();
    await flush();
    expect(FakeWebSocket.instances.length).toBe(1);
  });

  it("does not put a token in the url in cookie mode", async () => {
    const auth = makeAuth();
    const handle = watchDatastoreChanges("https://api.x.test", auth, "POD", {
      onChange: vi.fn(),
      useCookie: true,
    });
    await flush();
    expect(auth.getAccessToken).not.toHaveBeenCalled();
    expect(FakeWebSocket.instances[0].url).toBe(
      "wss://api.x.test/pods/POD/datastore/changes",
    );
    handle.close();
  });
});
