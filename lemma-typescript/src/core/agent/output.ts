// Agent final-output extraction pipeline.
//
// Pulling the result out of a finished agent run is genuinely fiddly: the answer
// can live in message metadata (`is_final_answer` + `structured_output`), in a
// `final_result`/`final_answer` tool call, in a raw tool message, or as JSON
// (sometimes fenced) in the assistant text. lemma-frontend grew a robust
// multi-path extractor for this; it's pure, so it lives here now and the product
// consumes it from the SDK — one implementation for hooks, web components, and
// the app. No React, no DOM.

import { normalizeAgentToolName } from "./tool-names.js";

export type MessagePartLike = {
  type?: string;
  text?: string;
  toolInvocation?: ToolInvocationLike;
};

export type ToolInvocationLike = {
  toolName?: string;
  tool_name?: string;
  args?: unknown;
  result?: unknown;
};

export type MessageLike = {
  id?: string;
  role?: unknown;
  // Renderable messages (from the SDK hooks) carry `content` as a string and
  // `parts`; raw flat conversation messages carry `kind` + `text` + tool fields.
  content?: unknown;
  kind?: string;
  text?: string | null;
  parts?: MessagePartLike[];
  toolInvocations?: ToolInvocationLike[];
  metadata?: unknown;
  message_metadata?: unknown;
  tool_name?: string | null;
  tool_call_id?: string | null;
  tool_args?: unknown;
  tool_result?: unknown;
  created_at?: string;
  createdAt?: Date;
};

export type AgentFinalOutput = Record<string, unknown>;

function isRecord(value: unknown): value is Record<string, unknown> {
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

function latestFirst<T extends MessageLike>(messages: T[]): T[] {
  const withTimestamps = messages.every((message) => createdAtMs(message) !== null);
  if (!withTimestamps) return [...messages].reverse();

  return [...messages].sort((a, b) => {
    const aTime = createdAtMs(a) ?? 0;
    const bTime = createdAtMs(b) ?? 0;
    return bTime - aTime;
  });
}

export function extractJsonObject(text: string): Record<string, unknown> | null {
  const trimmed = text.trim();
  if (!trimmed) return null;

  const fenced = trimmed.match(/```(?:json)?\s*([\s\S]*?)```/i);
  const objectStart = trimmed.indexOf("{");
  const objectEnd = trimmed.lastIndexOf("}");
  const candidates = [
    fenced?.[1],
    trimmed,
    objectStart >= 0 && objectEnd > objectStart ? trimmed.slice(objectStart, objectEnd + 1) : undefined,
  ].filter((candidate): candidate is string => !!candidate && candidate.trim().length > 0);

  for (const candidate of candidates) {
    try {
      const parsed = JSON.parse(candidate.trim());
      if (isRecord(parsed)) return parsed;
    } catch {
      // Try the next candidate.
    }
  }

  return null;
}

function messageTextCandidates(
  message: Pick<MessageLike, "content" | "parts" | "kind" | "text">,
  options: { includeReasoning?: boolean } = {},
): string[] {
  const candidates: string[] = [];

  // Flat conversation message: text/thinking/notification carry `text`.
  if (typeof message.text === "string" && message.text.trim()) {
    const kind = typeof message.kind === "string" ? message.kind : "";
    const shouldUseText = kind !== "TOOL_CALL" && kind !== "tool_call"
      && kind !== "TOOL_RETURN" && kind !== "tool_return"
      && (kind !== "THINKING" && kind !== "thinking" || options.includeReasoning === true);
    if (shouldUseText) {
      candidates.push(message.text.trim());
    }
  }

  // Renderable message: `content` is already a plain string.
  if (typeof message.content === "string" && message.content.trim()) {
    candidates.push(message.content.trim());
  }

  const textParts = (message.parts || [])
    .filter((part) => (
      (part.type === "text" || (options.includeReasoning === true && part.type === "reasoning"))
      && typeof part.text === "string"
    ))
    .map((part) => part.text?.trim())
    .filter((text): text is string => !!text);

  candidates.push(...textParts);

  return candidates.filter((candidate) => candidate.trim().length > 0);
}

export function extractMessageText(message: Pick<MessageLike, "content" | "parts">): string {
  return messageTextCandidates(message).join("\n\n").trim();
}

export function latestAssistantText(messages: MessageLike[]): string {
  for (const message of latestFirst(messages)) {
    const role = String(message.role || "").toLowerCase();
    if (role === "user") continue;

    const text = extractMessageText(message);
    if (text) return text;
  }

  return "";
}

export function isFinalResultToolName(toolName: string): boolean {
  const normalized = normalizeAgentToolName(toolName).toLowerCase().replace(/[.\-:]/g, "_");
  return normalized === "final_result" || normalized === "final_answer";
}

export function unwrapFinalResultPayload(value: unknown): unknown {
  if (!isRecord(value)) return value;

  const directKeys = [
    "structured_output",
    "structuredOutput",
    "output_data",
    "outputData",
    "final_result",
    "finalResult",
    "final_answer",
    "finalAnswer",
    "answer",
    "result",
    "output",
    "data",
    "value",
  ];

  for (const key of directKeys) {
    if (typeof value[key] !== "undefined") {
      return unwrapFinalResultPayload(value[key]);
    }
  }

  const operationalKeys = new Set([
    "success",
    "message",
    "error",
    "status",
    "final_answer_status",
    "finalAnswerStatus",
    "final_answer_tool_name",
    "finalAnswerToolName",
    "tool_name",
    "toolName",
    "tool_call_id",
    "toolCallId",
    "is_final_answer",
    "isFinalAnswer",
  ]);
  const meaningfulEntries = Object.entries(value).filter(([key]) => !operationalKeys.has(key));
  if (meaningfulEntries.length > 0) {
    return Object.fromEntries(meaningfulEntries);
  }

  if (typeof value.message === "string") {
    const parsed = extractJsonObject(value.message);
    return parsed ?? value.message;
  }

  return value;
}

function getMessageMetadata(message: MessageLike): Record<string, unknown> | null {
  if (isRecord(message.metadata)) return message.metadata;
  if (isRecord(message.message_metadata)) return message.message_metadata;
  return null;
}

function toFinalOutputRecord(value: unknown): AgentFinalOutput | null {
  const unwrapped = unwrapFinalResultPayload(value);
  if (isRecord(unwrapped)) return unwrapped;
  if (typeof unwrapped === "string") {
    const parsed = extractJsonObject(unwrapped);
    return parsed ?? { result: unwrapped };
  }
  if (Array.isArray(unwrapped)) return { result: unwrapped };
  if (typeof unwrapped === "number" || typeof unwrapped === "boolean") return { result: unwrapped };
  return null;
}

function finalOutputFromMetadata(messages: MessageLike[]): AgentFinalOutput | null {
  for (const message of latestFirst(messages)) {
    const metadata = getMessageMetadata(message);
    if (metadata?.is_final_answer !== true && metadata?.isFinalAnswer !== true) continue;

    const structured = toFinalOutputRecord(metadata.structured_output ?? metadata.structuredOutput);
    if (structured) return structured;

    const content = typeof message.text === "string"
      ? message.text
      : typeof message.content === "string"
        ? message.content
        : "";
    const parsed = extractJsonObject(content);
    if (parsed) return parsed;
  }

  return null;
}

function finalOutputFromMarkedText(messages: MessageLike[]): AgentFinalOutput | null {
  for (const message of latestFirst(messages)) {
    const metadata = getMessageMetadata(message) || {};
    const toolName = String(message.tool_name ?? metadata.tool_name ?? "");
    const isMarkedFinal = metadata.is_final_answer === true
      || metadata.isFinalAnswer === true
      || isFinalResultToolName(toolName);
    if (!isMarkedFinal) continue;

    for (const text of messageTextCandidates(message)) {
      const parsed = extractJsonObject(text);
      if (parsed) return parsed;
    }
  }

  return null;
}

function finalOutputFromToolInvocations(messages: MessageLike[]): AgentFinalOutput | null {
  const toolInvocations = messages.flatMap((message) => [
    ...((message.parts || [])
      .filter((part) => part.type === "tool")
      .map((part) => part.toolInvocation)
      .filter(Boolean) as ToolInvocationLike[]),
    ...(message.toolInvocations || []),
  ]);

  for (const invocation of [...toolInvocations].reverse()) {
    const toolName = String(invocation.toolName ?? invocation.tool_name ?? "");
    if (!isFinalResultToolName(toolName)) continue;

    const candidates = [invocation.result, invocation.args]
      .map(toFinalOutputRecord)
      .filter((candidate): candidate is AgentFinalOutput => candidate !== null);

    if (candidates.length > 0) return candidates[0];
  }

  return null;
}

function finalOutputFromRawToolMessages(messages: MessageLike[]): AgentFinalOutput | null {
  for (const message of latestFirst(messages)) {
    const metadata = getMessageMetadata(message) || {};
    const toolName = String(message.tool_name ?? metadata.tool_name ?? "");
    if (!isFinalResultToolName(toolName)) continue;

    const candidates = [
      message.tool_result,
      message.tool_args,
      metadata.structured_output,
      metadata.structuredOutput,
    ].map(toFinalOutputRecord);

    const output = candidates.find((candidate): candidate is AgentFinalOutput => candidate !== null);
    if (output) return output;
  }

  return null;
}

function finalOutputFromJsonText(messages: MessageLike[]): AgentFinalOutput | null {
  for (const message of latestFirst(messages)) {
    const role = String(message.role || "").toLowerCase();
    if (role === "user") continue;

    for (const text of messageTextCandidates(message, { includeReasoning: true })) {
      const parsed = extractJsonObject(text);
      if (parsed) return parsed;
    }
  }

  return null;
}

/**
 * Extract the structured final output of an agent run, trying each known place
 * the answer can hide, in order of reliability: message metadata → final-result
 * tool invocation → raw tool message → text marked final → (opt-in) any JSON in
 * the assistant text.
 */
export function extractAgentFinalOutput(
  messages: MessageLike[],
  options: { parseTextFallback?: boolean } = {},
): AgentFinalOutput | null {
  return finalOutputFromMetadata(messages)
    ?? finalOutputFromToolInvocations(messages)
    ?? finalOutputFromRawToolMessages(messages)
    ?? finalOutputFromMarkedText(messages)
    ?? (options.parseTextFallback ? finalOutputFromJsonText(messages) : null);
}
