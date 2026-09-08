// Framework-agnostic conversation-message helpers.
//
// These are pure (no React, no DOM) so they can power the React hooks, the
// AgentController, and — eventually — vanilla/web-component surfaces from a
// single implementation. Re-exported from `react/assistant-output.ts` for
// back-compat.
import type { ConversationMessage } from "../../types.js";

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

export function normalizeConversationStatus(status: unknown): string | undefined {
  if (typeof status !== "string") return undefined;
  const normalized = status.trim().toUpperCase();
  return normalized.length > 0 ? normalized : undefined;
}

export function isConversationRunningStatus(status: unknown): boolean {
  const normalized = normalizeConversationStatus(status);
  if (!normalized) return false;
  return normalized === "RUNNING" || normalized === "IN_PROGRESS" || normalized === "PROCESSING" || normalized === "STOP_REQUESTED";
}

function extractTextFromStructuredContentEntry(entry: unknown): string {
  if (typeof entry === "string") return entry.trim();
  if (!isRecord(entry)) return "";

  if (typeof entry.text === "string") return entry.text.trim();
  if (typeof entry.content === "string") return entry.content.trim();
  if (typeof entry.value === "string") return entry.value.trim();

  if (Array.isArray(entry.content)) {
    const nested = entry.content
      .map((child) => extractTextFromStructuredContentEntry(child))
      .filter((text) => text.length > 0)
      .join("\n")
      .trim();
    if (nested.length > 0) return nested;
  }

  if (Array.isArray(entry.summary)) {
    const summary = entry.summary
      .map((child) => extractTextFromStructuredContentEntry(child))
      .filter((text) => text.length > 0)
      .join("\n")
      .trim();
    if (summary.length > 0) return summary;
  }

  return "";
}

export function extractConversationMessageText(content: unknown): string {
  if (typeof content === "string") return content.trim();

  if (Array.isArray(content)) {
    return content
      .map((entry) => extractTextFromStructuredContentEntry(entry))
      .filter((text) => text.length > 0)
      .join("\n\n")
      .trim();
  }

  if (!isRecord(content)) return "";

  const directContent = content.content;
  if (typeof directContent === "string") return directContent.trim();
  if (Array.isArray(directContent)) {
    const text = directContent
      .map((entry) => extractTextFromStructuredContentEntry(entry))
      .filter((entry) => entry.length > 0)
      .join("\n\n")
      .trim();
    if (text.length > 0) return text;
  }

  if (typeof content.text === "string") return content.text.trim();

  return extractTextFromStructuredContentEntry(content);
}

/**
 * Display text for a flat conversation message. Text/thinking/notification
 * messages carry their body under `text`; tool_return messages may carry a
 * human-readable payload under `tool_result`.
 */
export function conversationMessageText(
  message: Pick<ConversationMessage, "text" | "tool_result"> | null | undefined,
): string {
  if (!message) return "";
  if (typeof message.text === "string" && message.text.trim().length > 0) {
    return message.text.trim();
  }
  if (message.tool_result !== undefined && message.tool_result !== null) {
    return extractConversationMessageText(message.tool_result);
  }
  return "";
}

export function sortConversationMessagesByCreatedAt(messages: ConversationMessage[]): ConversationMessage[] {
  return [...messages].sort((a, b) => {
    const aTime = Number.isFinite(new Date(a.created_at).getTime()) ? new Date(a.created_at).getTime() : 0;
    const bTime = Number.isFinite(new Date(b.created_at).getTime()) ? new Date(b.created_at).getTime() : 0;
    return aTime - bTime;
  });
}

export function getLatestAssistantMessage(messages: ConversationMessage[]): ConversationMessage | null {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (typeof message?.role === "string" && message.role.toLowerCase() === "assistant") {
      return message;
    }
  }

  return null;
}
