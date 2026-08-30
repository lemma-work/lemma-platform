import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { LemmaClient } from "../client.js";
import type { Conversation } from "../types.js";
import { useAssistantController, type UseAssistantControllerResult } from "../react/index.js";

/**
 * Sending a follow-up into a run that is already working.
 *
 * The hazard this guards is not "does the message arrive" — it is *how*.
 * Calling `sendMessage` again mid-run cancels the stream carrying the answer
 * and opens a second subscription for the same run, so everything between the
 * two is lost. `steerMessage` persists the message and reattaches instead.
 */

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const roots: Root[] = [];

const POD_CWD = "/me/c/2026-08-30/ab12cd34";

function conversation(id: string, status: string): Conversation {
  return {
    id,
    pod_id: "pod-1",
    title: id,
    status,
    pod_cwd: POD_CWD,
    created_at: "2026-08-30T12:00:00.000Z",
    updated_at: "2026-08-30T12:00:00.000Z",
  } as Conversation;
}

function neverEndingStream(): ReadableStream<Uint8Array> {
  // A run that is still working: no terminal frame, nothing to close it.
  return new ReadableStream<Uint8Array>({ start() {} });
}

function finishedTurn(): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(encoder.encode(
        `data: {"type":"completed","data":{"conversation_status":"WAITING"}}\n\n`,
      ));
      controller.close();
    },
  });
}

function fakeClient(options: { status?: string; podCwd?: string | null } = {}) {
  const running = conversation("c1", options.status ?? "RUNNING");
  if (options.podCwd !== undefined) {
    (running as { pod_cwd?: string | null }).pod_cwd = options.podCwd;
  }

  const appendMessage = vi.fn(
    async (_conversationId: string, _payload: { content?: string }, _options?: unknown) => ({
      conversation_id: "c1",
      agent_run_id: "run-1",
      started_new_run: false,
    }),
  );
  // A send owns its stream and that stream ends with the turn; a resume
  // attaches to a run still working, so it stays open.
  const sendMessageStream = vi.fn(async () => finishedTurn());
  const resumeStream = vi.fn(async () => neverEndingStream());
  const get = vi.fn(async () => running);
  const upload = vi.fn(async (file: File, _options?: { directoryPath?: string }) => ({
    id: `f-${file.name}`,
    name: file.name,
    path: `${POD_CWD}/${file.name}`,
    mime_type: "text/plain",
  }));
  const folderCreate = vi.fn(async () => ({}));

  const client = {
    podId: "pod-1",
    withPod() {
      return this;
    },
    conversations: {
      list: vi.fn(async () => ({ items: [running], limit: 30, next_page_token: null })),
      get,
      create: vi.fn(async () => running),
      listModels: vi.fn(async () => ({ items: [] })),
      messages: { list: vi.fn(async () => ({ items: [], limit: 100, next_page_token: null })) },
      sendMessageStream,
      appendMessage,
      retryFailedRun: vi.fn(),
      resumeStream,
      stopRun: vi.fn(),
      update: vi.fn(),
    },
    files: { upload, folder: { create: folderCreate } },
  } as unknown as LemmaClient;

  return { client, appendMessage, sendMessageStream, resumeStream, get, upload, folderCreate };
}

async function settle() {
  for (let index = 0; index < 6; index += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 1));
    });
  }
}

async function mount(client: LemmaClient) {
  const controller = { current: null as UseAssistantControllerResult | null };

  function Harness() {
    controller.current = useAssistantController({ client, podId: "pod-1", autoLoadMessages: true });
    return null;
  }

  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  roots.push(root);
  await act(async () => {
    root.render(createElement(Harness));
  });
  await settle();
  await act(async () => {
    controller.current?.selectConversation("c1");
  });
  await settle();
  return controller;
}

afterEach(async () => {
  while (roots.length > 0) {
    const root = roots.pop();
    if (!root) continue;
    await act(async () => root.unmount());
  }
  document.body.innerHTML = "";
  vi.restoreAllMocks();
});

describe("steering a run that is already working", () => {
  it("appends the message instead of opening a second stream for the same run", async () => {
    const { client, appendMessage, sendMessageStream } = fakeClient();
    const controller = await mount(client);

    await act(async () => {
      await controller.current?.steerMessage("also check the invoices");
    });
    await settle();

    expect(appendMessage).toHaveBeenCalledTimes(1);
    expect(appendMessage.mock.calls[0]?.[1]).toMatchObject({ content: "also check the invoices" });
    // The whole point: no second send-stream for a run that already has one.
    expect(sendMessageStream).not.toHaveBeenCalled();
  });

  it("puts the turn on screen before the server has echoed it", async () => {
    const { client } = fakeClient();
    const controller = await mount(client);

    await act(async () => {
      await controller.current?.steerMessage("one more thing");
    });
    await settle();

    const texts = (controller.current?.messages ?? []).map((message) => message.content);
    expect(texts).toContain("one more thing");
  });

  it("leaves the live stream alone rather than subscribing to the run twice", async () => {
    // The reconnect after a steer carries `force`, because the dedup key is
    // conversation+status and a steer changes neither — a run whose stream had
    // died looked identical to one still being watched. `force` must not cost
    // a duplicate subscription while a stream is genuinely alive, which is what
    // this pins; the dead-stream half is covered at the session level in
    // assistant-session-recovery.test.ts.
    const { client, resumeStream } = fakeClient();
    const controller = await mount(client);
    const beforeSteers = resumeStream.mock.calls.length;

    await act(async () => {
      await controller.current?.steerMessage("first");
    });
    await settle();
    await act(async () => {
      await controller.current?.steerMessage("second");
    });
    await settle();

    expect(resumeStream.mock.calls.length).toBe(beforeSteers);
  });

  it("carries attachments, rather than dropping them without a word", async () => {
    const { client, appendMessage, upload } = fakeClient();
    const controller = await mount(client);

    await act(async () => {
      await controller.current?.uploadFiles(
        [new File(["hello"], "report.txt", { type: "text/plain" })],
        { deferUntilSend: true },
      );
    });
    await settle();
    await act(async () => {
      await controller.current?.steerMessage("use this too");
    });
    await settle();

    expect(upload).toHaveBeenCalledTimes(1);
    const content = String(appendMessage.mock.calls[0]?.[1]?.content ?? "");
    expect(content).toContain("use this too");
    expect(content).toContain("report.txt");
  });
});

describe("where an attachment lands", () => {
  it("uploads into the conversation's own working directory", async () => {
    // Not `/me/conversations/{uuid}`: that directory is not the one the agent's
    // pod tools resolve a relative path against, so `report.txt` was findable
    // only by the absolute path pasted into the message text.
    const { client, upload, folderCreate } = fakeClient({ status: "WAITING" });
    const controller = await mount(client);

    await act(async () => {
      await controller.current?.uploadFiles(
        [new File(["hello"], "report.txt", { type: "text/plain" })],
        { deferUntilSend: true },
      );
    });
    await settle();
    await act(async () => {
      await controller.current?.sendMessage("read the attachment");
    });
    await settle();

    expect(upload).toHaveBeenCalledTimes(1);
    expect(upload.mock.calls[0]?.[1]).toMatchObject({ directoryPath: POD_CWD });
    // The upload endpoint creates missing parents itself, so the two folder
    // round-trips that used to precede every attachment are gone.
    expect(folderCreate).not.toHaveBeenCalled();
  });
});
