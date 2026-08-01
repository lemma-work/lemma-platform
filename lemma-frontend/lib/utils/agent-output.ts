// The pure agent final-output extraction pipeline now lives in the SDK core
// (lemma-sdk) so the app, the hooks, and apps share one implementation. This
// module re-exports it and keeps the product-specific helpers (conversation
// shapes, schema field ordering, value preview).
import {
  extractAgentFinalOutput,
  extractMessageText,
  extractJsonObject,
  latestAssistantText,
  isFinalResultToolName,
  unwrapFinalResultPayload,
} from 'lemma-sdk';
import type { AgentFinalOutput, MessageLike } from 'lemma-sdk';

export {
  extractAgentFinalOutput,
  extractMessageText,
  extractJsonObject,
  latestAssistantText,
  isFinalResultToolName,
  unwrapFinalResultPayload,
};
export type { AgentFinalOutput, MessageLike };

export function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function createdAtMs(message: MessageLike): number | null {
  const raw = message.createdAt instanceof Date
    ? message.createdAt.toISOString()
    : message.created_at;
  if (!raw) return null;
  const timestamp = new Date(raw).getTime();
  return Number.isFinite(timestamp) ? timestamp : null;
}

export function firstUserInput(messages: MessageLike[]): Record<string, unknown> | null {
  const ordered = [...messages].sort((a, b) => {
    const aTime = createdAtMs(a);
    const bTime = createdAtMs(b);
    if (aTime !== null && bTime !== null) return aTime - bTime;
    return 0;
  });
  const firstUser = ordered.find((message) => String(message.role || "").toLowerCase() === "user");
  if (!firstUser) return null;
  return extractJsonObject(extractMessageText(firstUser));
}

export function isTaskConversationLike(conversation: unknown): boolean {
  if (!isRecord(conversation)) return false;
  return String(conversation.type || "").trim().toUpperCase() === "TASK";
}

export function taskConversationOutput(conversation: unknown): AgentFinalOutput | null {
  if (!isTaskConversationLike(conversation) || !isRecord(conversation)) return null;

  const output = conversation.output;
  if (isRecord(output)) return output;
  if (typeof output === "string") {
    const parsed = extractJsonObject(output);
    return parsed ?? (output.trim() ? { result: output } : null);
  }
  if (Array.isArray(output)) return output.length > 0 ? { result: output } : null;
  if (typeof output === "number" || typeof output === "boolean") return { result: output };
  return null;
}
