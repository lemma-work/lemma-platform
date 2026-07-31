import type { ConversationMessage } from "./types.js";

interface ParsedRecord {
  [key: string]: unknown;
}

const CONVERSATION_STATUS_ALIASES: Record<string, string> = {
  RUNNING: "RUNNING",
  IN_PROGRESS: "IN_PROGRESS",
  PROCESSING: "PROCESSING",
  STOP_REQUESTED: "STOP_REQUESTED",
  WAITING: "WAITING",
  COMPLETED: "COMPLETED",
  COMPLETE: "COMPLETED",
  DONE: "COMPLETED",
  FAILED: "FAILED",
  ERROR: "FAILED",
  STOPPED: "STOPPED",
  CANCELLED: "STOPPED",
  CANCELED: "STOPPED",
};

export interface ParsedAssistantStreamEvent {
  message?: ConversationMessage;
  status?: string;
  token?: string;
  tokenKind?: string;
  error?: string;
}

function isRecord(value: unknown): value is ParsedRecord {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function normalizeStatus(status: unknown): string | undefined {
  if (typeof status !== "string") return undefined;
  const normalized = status.trim().toUpperCase().replace(/[-\s]+/g, "_");
  return CONVERSATION_STATUS_ALIASES[normalized];
}

function toConversationMessage(value: unknown): ConversationMessage | undefined {
  if (!isRecord(value)) return undefined;
  if (typeof value.id !== "string") return undefined;
  if (typeof value.role !== "string") return undefined;
  // Flat shape: every persisted message carries a `kind` discriminator. The old
  // nested `content` object is gone.
  if (typeof value.kind !== "string") return undefined;

  const message: ConversationMessage = {
    id: value.id,
    role: value.role,
    kind: value.kind as ConversationMessage["kind"],
    text: typeof value.text === "string" ? value.text : null,
    tool_name: typeof value.tool_name === "string" ? value.tool_name : null,
    tool_call_id: typeof value.tool_call_id === "string" ? value.tool_call_id : null,
    tool_args: "tool_args" in value ? value.tool_args : null,
    tool_result: "tool_result" in value ? value.tool_result : null,
    created_at: typeof value.created_at === "string" ? value.created_at : new Date().toISOString(),
    conversation_id: typeof value.conversation_id === "string" ? value.conversation_id : undefined,
    sequence: typeof value.sequence === "number" ? value.sequence : undefined,
    agent_run_id: typeof value.agent_run_id === "string" ? value.agent_run_id : null,
    metadata: isRecord(value.metadata) ? value.metadata : null,
  };

  return message;
}

function extractPayload(record: ParsedRecord): unknown {
  if ("data" in record) return record.data;
  if ("payload" in record) return record.payload;
  return undefined;
}

function extractStatus(payload: unknown): string | undefined {
  if (isRecord(payload)) {
    return normalizeStatus(payload.status)
      ?? normalizeStatus(payload.conversation_status)
      ?? normalizeStatus(payload.run_status)
      ?? (isRecord(payload.conversation) ? normalizeStatus(payload.conversation.status) : undefined);
  }

  return normalizeStatus(payload);
}

function extractErrorMessage(payload: unknown): string | undefined {
  if (typeof payload === "string") {
    const message = payload.trim();
    return message.length > 0 ? message : undefined;
  }

  if (isRecord(payload)) {
    const message = [
      payload.message,
      payload.error,
      payload.detail,
      payload.reason,
      payload.description,
    ].find((value) => typeof value === "string" && value.trim().length > 0);

    if (typeof message === "string") {
      return message.trim();
    }
  }

  return undefined;
}

export function parseAssistantStreamEvent(value: unknown): ParsedAssistantStreamEvent {
  const directMessage = toConversationMessage(value);
  if (directMessage) {
    return { message: directMessage };
  }

  if (!isRecord(value)) {
    return {};
  }

  const eventType = typeof value.type === "string" ? value.type.toLowerCase() : "";
  const payload = extractPayload(value);

  if (eventType === "token" && typeof payload === "string") {
    const tokenKind = typeof value.kind === "string" && value.kind.trim()
      ? value.kind.trim().toLowerCase()
      : "text";
    return { token: payload, tokenKind };
  }

  if (eventType === "message" || eventType === "message_added") {
    const message = toConversationMessage(payload);
    return message ? { message } : {};
  }

  if (
    eventType === "status"
    || eventType === "conversation_status"
    || eventType === "conversation_updated"
    || eventType === "run_status"
  ) {
    const status = extractStatus(payload);
    return status ? { status } : {};
  }

  if (eventType === "completed") {
    // A run can finish (`completed`) while the conversation is left WAITING
    // (the agent paused on ask_user / request_approval). The conversation status
    // is the user-facing truth, so prefer it over the run's terminal status.
    const conversationStatus = isRecord(payload)
      ? normalizeStatus(payload.conversation_status)
      : undefined;
    const status = conversationStatus ?? extractStatus(payload) ?? "COMPLETED";
    return { status };
  }

  if (eventType === "stopped") {
    return { status: "STOPPED" };
  }

  if (eventType === "error" || eventType === "stream_error") {
    return {
      status: "FAILED",
      error: extractErrorMessage(payload) ?? "Agent run failed.",
    };
  }

  return {};
}

export function upsertConversationMessage(
  messages: ConversationMessage[],
  incoming: ConversationMessage,
): ConversationMessage[] {
  const next = [...messages];
  const index = next.findIndex((message) => message.id === incoming.id);

  if (index >= 0) {
    next[index] = incoming;
  } else {
    next.push(incoming);
  }

  next.sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime());
  return next;
}
