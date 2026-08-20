// Turn model for the conversation surface.
//
// The transcript the SDK hands us is a list of display ROWS — a faithful,
// chronological record of everything the run did (text, thoughts, tool calls,
// merged clusters). A conversation is not a log: it is a sequence of *turns*,
// each one ask → work → result. This adapter is the boundary between the two.
//
// What it does, per turn:
//   - the user's message stays the turn's opener
//   - assistant speech stays speech: narration beats (intermediate messages,
//     or traceNote reasoning once the SDK folds a finished run) and the final
//     answer all render as bubbles/cards — they are never demoted to a trace
//   - tool work and genuine thinking collect into one collapsible trace, which
//     the UI renders as a single left-aligned status pill ("Worked for 9m 14s")
//   - files the run produced (presented via display_resource, or written with a
//     deliverable extension) become artifact cards; other display_resource calls
//     become resource cards
//   - ask_user / request_approval calls become in-chat interaction cards
//
// Pure and framework-free so the grouping rules are unit-testable without
// rendering anything.

import {
  dedupToolInvocations,
  formatDurationCompact,
  isFinalResultToolName,
  isRenderableUserInteractionInvocation,
  messageTextContent,
  messageTimeMs,
  normalizeAssistantDisplayText,
  userApprovalResolvedDecision,
  type AssistantMessagePart,
  type AssistantRenderableMessage,
  type AssistantToolInvocation,
  type DisplayMessageRow,
} from "lemma-sdk";
import {
  buildDisplayResourceHref,
  extractDisplayResourceFromInvocation,
  isDisplayResourceToolName,
  type DisplayResourceRequest,
} from "@/lib/assistant/display-resource";
import { isSubagentLifecycleToolName } from "@/lib/assistant/subagent-activity";

// --- public types -----------------------------------------------------------

export type ToolPart = Extract<AssistantMessagePart, { type: "tool" }>;

export type TraceEntry =
  | { kind: "tool"; id: string; invocation: AssistantToolInvocation; message: AssistantRenderableMessage }
  | { kind: "thinking"; id: string; text: string; streaming: boolean };

export type ChatTurnItem =
  | {
    kind: "text";
    id: string;
    text: string;
    /** Answer-weight speech (not a mid-run narration beat). */
    answer: boolean;
    /** Stamped `is_final_answer` by the backend — the strongest result signal. */
    final: boolean;
    /**
     * Only the turn's result text may render as a doc card: the flagged final
     * answer when one exists, otherwise the closing run of answer text. A long
     * mid-turn beat is speech, and speech stays a bubble.
     */
    documentEligible: boolean;
    streaming: boolean;
  }
  | { kind: "notice"; id: string; text: string }
  | { kind: "interaction"; id: string; invocation: AssistantToolInvocation; message: AssistantRenderableMessage };

export interface ChatArtifact {
  /** Dedupe key — the pod path. */
  key: string;
  path: string;
  fileName: string;
  /** Humanized label ("Hermes Agent Team Brief"). */
  name: string;
  /** Uppercase extension badge label ("PDF"). */
  ext: string;
  kind: "video" | "image" | "file";
  sizeBytes?: number;
  /** In-app href to the file in the pod's files view. */
  href: string | null;
  /** The call that produced or presented it — the presentation stage keys off it. */
  toolCallId: string | null;
}

export interface ChatResourceCard {
  toolCallId: string;
  request: DisplayResourceRequest;
  href: string | null;
}

export interface ChatTurn {
  id: string;
  userMessage: AssistantRenderableMessage | null;
  items: ChatTurnItem[];
  trace: TraceEntry[];
  subagentParts: ToolPart[];
  artifacts: ChatArtifact[];
  resources: ChatResourceCard[];
  /** First assistant activity in the turn (ms since epoch), null when it never worked. */
  startedAtMs: number | null;
  /** Last assistant activity in the turn. */
  endedAtMs: number | null;
  toolCount: number;
  thinkingCount: number;
  failedCount: number;
  isLive: boolean;
  hasPendingInteraction: boolean;
}

// --- classification tables --------------------------------------------------

const FILE_WRITE_TOOLS = new Set(["pod_write_file", "create_file"]);

const VIDEO_EXTENSIONS = new Set(["mp4", "webm", "mov", "m4v"]);
const IMAGE_EXTENSIONS = new Set(["png", "jpg", "jpeg", "gif", "webp", "svg"]);

// A written file only earns a card when it looks like a deliverable. Build
// scripts and scratch output stay in the trace — otherwise a run that wrote
// twelve files to produce two would end with fourteen cards.
const DELIVERABLE_EXTENSIONS = new Set([
  "pdf", "pptx", "docx", "xlsx", "csv", "tsv", "md", "html", "epub",
  "mp3", "wav", "ogg", "flac", "zip",
  ...VIDEO_EXTENSIONS,
  ...IMAGE_EXTENSIONS,
]);

// A long or structured answer reads as a document; a short one as a chat
// bubble. The rule is a pure function of the text, so a streaming answer and
// its settled form classify identically the moment the text crosses a line.
const DOC_MIN_LENGTH = 700;
export function answerIsDocument(text: string): boolean {
  if (text.length > DOC_MIN_LENGTH) return true;
  if (/^#{1,4}\s/m.test(text)) return true;
  if (/^\s*\|[^\n]+\|\s*$/m.test(text)) return true;
  const bullets = text.match(/^\s*(?:[-*•]|\d+[.)])\s+/gm);
  return (bullets?.length ?? 0) >= 3;
}

// --- small local helpers (kept dependency-free on purpose) ------------------

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function fileNameFromPath(path: string): string {
  const parts = path.replace(/\\/g, "/").split("/").filter(Boolean);
  return parts[parts.length - 1] || path;
}

function extensionOf(fileName: string): string {
  const dot = fileName.lastIndexOf(".");
  return dot > 0 ? fileName.slice(dot + 1).toLowerCase() : "";
}

function humanizeFileName(fileName: string): string {
  const cleaned = fileName
    .replace(/\.[^.]+$/, "")
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  if (!cleaned) return fileName;
  return cleaned.charAt(0).toUpperCase() + cleaned.slice(1);
}

function normalizeName(toolName: string): string {
  return toolName.trim().toLowerCase();
}

/**
 * Metadata flags, read the way the wire actually carries them: snake or camel
 * case, on the message's content record or either metadata field. Trusting one
 * spelling left older or non-Agent-Host history unflagged, and unflagged
 * narration used to render at answer weight.
 */
function messageFlag(
  message: AssistantRenderableMessage,
  snakeKey: string,
  camelKey: string,
): boolean {
  const content = record(message.content);
  return content[snakeKey] === true
    || content[camelKey] === true
    || record(message.metadata)[snakeKey] === true
    || record(message.metadata)[camelKey] === true
    || record(message.message_metadata)[snakeKey] === true
    || record(message.message_metadata)[camelKey] === true;
}

function interactionIsPending(invocation: AssistantToolInvocation): boolean {
  if (invocation.state === "result") return false;
  const result = record(invocation.result);
  if (userApprovalResolvedDecision(result)) return false;
  // ask_user resolves with an `answers` payload even on dismissal.
  if (record(result.answers) && Object.keys(record(result.answers)).length > 0) return false;
  return true;
}

/** The parts a message renders from, mirroring MessageGroup's fallback so
 * messages that never got a parts array still classify correctly. */
function partsOfMessage(message: AssistantRenderableMessage): AssistantMessagePart[] {
  if (message.parts && message.parts.length > 0) return message.parts;
  const text = normalizeAssistantDisplayText(
    typeof message.content === "string" ? message.content : messageTextContent(message),
  );
  return [
    ...(text
      ? [{ id: `${message.id}-fallback-text`, type: "text", text } as AssistantMessagePart]
      : []),
    ...(message.toolInvocations || []).map((tool, index) => ({
      id: `${tool.toolCallId || message.id}-fallback-tool-${index}`,
      type: "tool",
      toolInvocation: tool,
    } as AssistantMessagePart)),
  ];
}

// --- the adapter --------------------------------------------------------------

export interface BuildChatTurnsOptions {
  rows: DisplayMessageRow[];
  /** Raw messages, for timing: a clustered row keeps only its newest
   * message's timestamp, so durations resolve against the source messages. */
  messages: AssistantRenderableMessage[];
  isRunActive: boolean;
  podId: string | null;
  conversationId: string | null;
}

interface MutableTurn extends ChatTurn {
  artifactByPath: Map<string, ChatArtifact>;
  resourceIds: Set<string>;
}

// The SDK's runtime store shows the user's message before the server confirms
// it, under a provisional id (`optimistic-user-…`, see
// lemma-typescript/src/react/useAssistantRuntime.ts); when the echo arrives it
// replaces the message in place, changing the id. Turns are keyed by that id
// and the transcript keys turns, so the swap would remount the live turn and
// replay its entrance animations. Remember the turn id the provisional message
// had so its echo — and every rebuild after it — can inherit it.
//
// The memory key is conversation + content + a coarse time bucket: the echo's
// server timestamp and the provisional message's client timestamp differ by
// latency and clock skew, so an exact timestamp would miss. Buckets the size
// of the SDK's own optimistic match window, checked ±1 on lookup, cover it.
const OPTIMISTIC_USER_ID_PREFIX = "optimistic-user-";
const OPTIMISTIC_TURN_ID_LIMIT = 256;
const OPTIMISTIC_TURN_BUCKET_MS = 2 * 60 * 1000;
const optimisticTurnIds = new Map<string, string>();

function optimisticTurnKey(
  conversationId: string,
  content: string,
  timestampMs: number,
  bucketOffset = 0,
): string {
  const bucket = Math.floor(timestampMs / OPTIMISTIC_TURN_BUCKET_MS) + bucketOffset;
  return `${conversationId}\u0000${content}\u0000${bucket}`;
}

function turnIdForUserMessage(
  message: AssistantRenderableMessage,
  fallbackId: string,
  conversationId: string | null,
  taken: { id: string }[],
): string {
  const messageId = message.id || fallbackId;
  const generated = `turn-${messageId}`;
  const content = typeof message.content === "string" ? message.content : messageTextContent(message);
  if (!conversationId || !content) return generated;
  const timestampMs = messageTimeMs(message);
  if (timestampMs === null) return generated;

  if (messageId.startsWith(OPTIMISTIC_USER_ID_PREFIX)) {
    // Set-if-absent: with two identical provisional messages pending, the key
    // belongs to the first one, so the first echo inherits the first turn.
    const key = optimisticTurnKey(conversationId, content, timestampMs);
    if (!optimisticTurnIds.has(key)) optimisticTurnIds.set(key, generated);
    if (optimisticTurnIds.size > OPTIMISTIC_TURN_ID_LIMIT) {
      const oldest = optimisticTurnIds.keys().next().value;
      if (oldest !== undefined) optimisticTurnIds.delete(oldest);
    }
    return generated;
  }

  for (const bucketOffset of [0, -1, 1]) {
    const key = optimisticTurnKey(conversationId, content, timestampMs, bucketOffset);
    const inherited = optimisticTurnIds.get(key);
    if (!inherited) continue;
    // Delete-then-set refreshes the entry's position in the eviction order:
    // it must survive every rebuild for as long as this turn is on screen.
    optimisticTurnIds.delete(key);
    optimisticTurnIds.set(key, inherited);
    return taken.some((turn) => turn.id === inherited) ? generated : inherited;
  }
  return generated;
}

function newTurn(id: string, userMessage: AssistantRenderableMessage | null): MutableTurn {
  return {
    id,
    userMessage,
    items: [],
    trace: [],
    subagentParts: [],
    artifacts: [],
    resources: [],
    startedAtMs: null,
    endedAtMs: null,
    toolCount: 0,
    thinkingCount: 0,
    failedCount: 0,
    isLive: false,
    hasPendingInteraction: false,
    artifactByPath: new Map(),
    resourceIds: new Set(),
  };
}

export function buildChatTurns({
  rows,
  messages,
  isRunActive,
  podId,
  conversationId,
}: BuildChatTurnsOptions): ChatTurn[] {
  const turns: MutableTurn[] = [];
  let current: MutableTurn | null = null;
  const ensureTurn = (id: string): MutableTurn => {
    if (!current) {
      // A paginated window starts wherever the page boundary fell — usually
      // mid-turn, on assistant work whose ask is in an older page. The turn
      // still enters the list; a turn without its ask is a fine turn.
      current = newTurn(id, null);
      turns.push(current);
    }
    return current;
  };

  const lastRowIndex = rows.length - 1;

  const addArtifact = (
    turn: MutableTurn,
    artifact: { path: string; href: string | null; sizeBytes?: number; toolCallId: string },
  ) => {
    const fileName = fileNameFromPath(artifact.path);
    const ext = extensionOf(fileName);
    const existing = turn.artifactByPath.get(artifact.path);
    if (existing) {
      // A presented file and a written file are the same deliverable: merge,
      // preferring the presentation's href and keeping the write's size.
      existing.href = existing.href ?? artifact.href;
      existing.sizeBytes = existing.sizeBytes ?? artifact.sizeBytes;
      existing.toolCallId = artifact.toolCallId;
      return;
    }
    const entry: ChatArtifact = {
      key: artifact.path,
      path: artifact.path,
      fileName,
      name: humanizeFileName(fileName),
      ext: ext ? ext.toUpperCase() : "FILE",
      kind: VIDEO_EXTENSIONS.has(ext) ? "video" : IMAGE_EXTENSIONS.has(ext) ? "image" : "file",
      sizeBytes: artifact.sizeBytes,
      href: artifact.href,
      toolCallId: artifact.toolCallId,
    };
    turn.artifactByPath.set(artifact.path, entry);
    turn.artifacts.push(entry);
  };

  const fileHref = (toolCallId: string, path: string): string | null => {
    if (!podId) return null;
    return buildDisplayResourceHref({
      podId,
      request: { type: "FILE", path, loadingMessages: [] },
      conversationId,
      toolCallId,
    });
  };

  const messageMs = (message: AssistantRenderableMessage): number | null => {
    const ms = messageTimeMs(message);
    return typeof ms === "number" && !Number.isNaN(ms) ? ms : null;
  };

  const touchTimes = (turn: MutableTurn, message: AssistantRenderableMessage) => {
    const ms = messageMs(message);
    if (ms === null) return;
    turn.startedAtMs = turn.startedAtMs === null ? ms : Math.min(turn.startedAtMs, ms);
    turn.endedAtMs = turn.endedAtMs === null ? ms : Math.max(turn.endedAtMs, ms);
  };

  const classifyInvocation = (
    turn: MutableTurn,
    invocation: AssistantToolInvocation,
    message: AssistantRenderableMessage,
    partId: string,
  ) => {
    const toolName = normalizeName(invocation.toolName);

    if (isFinalResultToolName(invocation.toolName)) return;

    // display_resource is presentation, not work: cards, never a trace row.
    if (isDisplayResourceToolName(invocation.toolName)) {
      if (invocation.state === "result" && record(invocation.result).success !== false) {
        const displayResource = extractDisplayResourceFromInvocation(invocation);
        if (displayResource && !turn.resourceIds.has(displayResource.toolCallId)) {
          turn.resourceIds.add(displayResource.toolCallId);
          const href = podId
            ? buildDisplayResourceHref({
              podId,
              request: displayResource.request,
              conversationId,
              toolCallId: displayResource.toolCallId,
            })
            : null;
          if (displayResource.request.type === "FILE" && displayResource.request.path) {
            addArtifact(turn, { path: displayResource.request.path, href, toolCallId: displayResource.toolCallId });
          } else {
            turn.resources.push({ toolCallId: displayResource.toolCallId, request: displayResource.request, href });
          }
        }
      }
      return;
    }

    // Questions and approvals are conversation, not machinery: they render as
    // in-chat cards where the run paused.
    if (isRenderableUserInteractionInvocation(invocation)) {
      turn.items.push({ kind: "interaction", id: partId, invocation, message });
      if (interactionIsPending(invocation)) turn.hasPendingInteraction = true;
      touchTimes(turn, message);
      return;
    }

    // A written file that looks like a deliverable earns an artifact card —
    // even when the agent never formally presented it (the common case).
    if (FILE_WRITE_TOOLS.has(toolName) && invocation.state === "result") {
      const result = record(invocation.result);
      const path = typeof result.path === "string" && result.path
        ? result.path
        : typeof record(invocation.args).path === "string"
          ? record(invocation.args).path as string
          : null;
      const sizeBytes = typeof result.size_bytes === "number" ? result.size_bytes : undefined;
      if (path && result.success !== false && DELIVERABLE_EXTENSIONS.has(extensionOf(fileNameFromPath(path)))) {
        addArtifact(turn, { path, href: fileHref(invocation.toolCallId, path), sizeBytes, toolCallId: invocation.toolCallId });
      }
    }

    if (isSubagentLifecycleToolName(invocation.toolName)) {
      const part = (message.parts || []).find(
        (candidate): candidate is ToolPart => candidate.type === "tool"
          && candidate.toolInvocation.toolCallId === invocation.toolCallId,
      );
      if (part) turn.subagentParts.push(part);
    }

    turn.trace.push({ kind: "tool", id: partId, invocation, message });
    turn.toolCount += 1;
    if (
      invocation.state === "result"
      && record(invocation.result).success === false
    ) {
      turn.failedCount += 1;
    }
    touchTimes(turn, message);
  };

  rows.forEach((row, rowIndex) => {
    const message = row.message;

    if (message.role === "user") {
      const turnId = turnIdForUserMessage(message, `${row.id || rowIndex}`, conversationId, turns);
      current = newTurn(turnId, message);
      turns.push(current);
      return;
    }

    const isLastRow = rowIndex === lastRowIndex;
    const turn = ensureTurn(`turn-${row.id || rowIndex}`);

    // A clustered row's own timestamp is its newest message's; the turn's span
    // is the span of the row's source messages.
    for (const sourceIndex of row.sourceIndexes) {
      const source = messages[sourceIndex];
      if (source) touchTimes(turn, source);
    }

    // System notifications ("a schedule fired", "workflow finished") render as
    // centered separators, the way a messenger renders service messages.
    if (message.kind === "NOTIFICATION" || message.role === "system") {
      const text = normalizeAssistantDisplayText(messageTextContent(message) || message.content || "");
      if (text) turn.items.push({ kind: "notice", id: row.id, text });
      return;
    }

    // Speech is answer-weight unless the run flagged it as intermediate. The
    // doc-card decision is made later, per turn — never from this flag alone.
    const answerWeight = !messageFlag(
      message,
      "is_intermediate_assistant_message",
      "isIntermediateAssistantMessage",
    );

    for (const part of partsOfMessage(message)) {
      if (part.type === "text") {
        const text = normalizeAssistantDisplayText(part.text);
        if (!text) continue;
        turn.items.push({
          kind: "text",
          id: part.id,
          text,
          answer: answerWeight,
          final: messageFlag(message, "is_final_answer", "isFinalAnswer"),
          documentEligible: false,
          streaming: isRunActive && isLastRow,
        });
        touchTimes(turn, message);
        continue;
      }

      if (part.type === "reasoning") {
        const text = normalizeAssistantDisplayText(part.text);
        if (!text) continue;
        // Thinking is machinery and folds into the trace — including its
        // traceNote form, which is how the SDK folds a finished run's
        // THINKING messages. (Narration never folds: intermediate TEXT
        // messages stay text and surface as bubbles above.)
        //
        // messageWithTraceNote can leave the original reasoning part beside
        // the folded one; same text twice is one thought, not two.
        const isDuplicate = turn.trace.some((entry) => entry.kind === "thinking" && entry.text === text);
        if (isDuplicate) continue;
        turn.trace.push({
          kind: "thinking",
          id: part.id,
          text,
          streaming: part.state === "streaming",
        });
        turn.thinkingCount += 1;
        continue;
      }

      const invocation = dedupToolInvocations(message)
        .find((candidate) => candidate.toolCallId === part.toolInvocation.toolCallId)
        ?? part.toolInvocation;
      classifyInvocation(turn, invocation, message, part.id);
    }

    // Invocations that never appeared in `parts` (raw toolInvocations only).
    const partCallIds = new Set(
      partsOfMessage(message)
        .filter((part): part is ToolPart => part.type === "tool")
        .map((part) => part.toolInvocation.toolCallId),
    );
    for (const invocation of dedupToolInvocations(message)) {
      if (partCallIds.has(invocation.toolCallId)) continue;
      classifyInvocation(turn, invocation, message, `${message.id}-inv-${invocation.toolCallId}`);
    }
  });

  // Only the tail turn can be live: a running conversation is working on its
  // last turn by definition. A just-sent turn with no assistant output yet is
  // live too — its status pill is the only "Thinking" the transcript shows.
  turns.forEach((turn, index) => {
    turn.isLive = isRunActive && index === turns.length - 1;
  });

  // Document eligibility. The doc card is the *result* object: when the
  // backend flagged a final answer, only flagged text may use it. Older or
  // unflagged history falls back to the closing run of answer text. Either
  // way, a long mid-turn beat ("Let me check the connectors…" over four
  // paragraphs) is speech, and speech stays a bubble.
  for (const turn of turns) {
    const flaggedFinal = turn.items.filter(
      (item): item is Extract<ChatTurnItem, { kind: "text" }> => (
        item.kind === "text" && item.answer && item.final
      ),
    );
    if (flaggedFinal.length > 0) {
      flaggedFinal.forEach((item) => { item.documentEligible = true; });
      continue;
    }
    let index = turn.items.length - 1;
    while (index >= 0) {
      const item = turn.items[index];
      if (item.kind !== "text" || !item.answer) break;
      item.documentEligible = true;
      index -= 1;
    }
  }

  // Nothing to say and nothing to show: drop. (Empty turns are how stray
  // whitespace-only messages used to render as sliver bubbles.)
  const visibleTurns = turns.filter((turn) => (
    turn.isLive
    || turn.userMessage
    || turn.items.length > 0
    || turn.trace.length > 0
    || turn.artifacts.length > 0
    || turn.resources.length > 0
  ));

  return visibleTurns.map((turn) => {
    const { artifactByPath, resourceIds, ...rest } = turn;
    void artifactByPath;
    void resourceIds;
    return rest;
  });
}

/** The day a turn belongs to, for the thread's day separators. */
export function turnDayDate(turn: ChatTurn): Date | null {
  const userDate = turn.userMessage?.createdAt;
  if (userDate instanceof Date && !Number.isNaN(userDate.getTime())) return userDate;
  if (typeof turn.startedAtMs === "number") return new Date(turn.startedAtMs);
  return null;
}

export function sameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

/** "Today · 20 Aug" / "Yesterday · 19 Aug" / "Tue · 5 Aug" / "5 Aug 2025". */
export function dayMarkLabel(date: Date, now: Date = new Date()): string {
  const dayStart = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const diffDays = Math.round((dayStart(now) - dayStart(date)) / 86_400_000);
  const short = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short" }).format(date);
  if (diffDays === 0) return `Today · ${short}`;
  if (diffDays === 1) return `Yesterday · ${short}`;
  if (diffDays > 1 && diffDays < 7) {
    return `${new Intl.DateTimeFormat(undefined, { weekday: "short" }).format(date)} · ${short}`;
  }
  return new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", year: "numeric" }).format(date);
}

/** The folded label on a finished turn's status pill.
 *
 * No failure count: the label describes a turn that *completed* — if the
 * answer arrived, the run recovered, and a retried tool failure is trivia.
 * Failures still show on their rows inside the trace for anyone digging.
 *
 * No clock: a finished turn's duration is its own two timestamps — the
 * `?? nowMs` fallback this used to carry only made settled pills re-render
 * every second for a number that never changes. */
export function completedTurnStatusLabel(turn: ChatTurn): string | null {
  if (turn.trace.length === 0) return null;
  const durationMs = turn.startedAtMs !== null && turn.endedAtMs !== null
    ? Math.max(0, turn.endedAtMs - turn.startedAtMs)
    : null;
  const durationLabel = durationMs !== null && durationMs >= 1000
    ? formatDurationCompact(durationMs)
    : null;

  if (turn.toolCount === 0) {
    return durationLabel ? `Thought for ${durationLabel}` : "Thought";
  }
  if (durationLabel) {
    return `Worked for ${durationLabel} · ${turn.toolCount} step${turn.toolCount === 1 ? "" : "s"}`;
  }
  return `${turn.toolCount} step${turn.toolCount === 1 ? "" : "s"}`;
}

/** A render fingerprint for a turn.
 *
 * `buildChatTurns` rebuilds every turn object on every streaming flush, so
 * object identity cannot say whether a turn changed — this string can. Two
 * turns with equal fingerprints render identically, which lets the memoized
 * turn view skip the transcript's history while the live turn streams.
 *
 * Text is fingerprinted by length and flags rather than content: streaming is
 * append-only, so a growing bubble changes length, and the moment it settles
 * its `streaming` flag flips. Comparing the full text of every turn on every
 * flush would cost more than the re-render it saves. */
export function chatTurnFingerprint(turn: ChatTurn): string {
  const items = turn.items.map((item) => {
    if (item.kind === "text") {
      return `${item.id}:t:${item.text.length}:${item.streaming ? 1 : 0}:${item.answer ? 1 : 0}:${item.documentEligible ? 1 : 0}:${item.final ? 1 : 0}`;
    }
    if (item.kind === "interaction") {
      return `${item.id}:i:${item.invocation.state}`;
    }
    return `${item.id}:n:${item.text}`;
  }).join(",");
  const trace = turn.trace.map((entry) => (
    entry.kind === "tool"
      ? `${entry.invocation.toolCallId}:${entry.invocation.state}`
      : `${entry.id}:${entry.streaming ? 1 : 0}:${entry.text.length}`
  )).join(",");
  const subagents = turn.subagentParts.map((part) => `${part.toolInvocation.toolCallId}:${part.toolInvocation.state}`).join(",");
  const artifacts = turn.artifacts.map((artifact) => artifact.key).join(",");
  const resources = turn.resources.map((card) => card.toolCallId).join(",");
  return [
    turn.id,
    turn.isLive ? 1 : 0,
    turn.startedAtMs ?? "",
    turn.endedAtMs ?? "",
    turn.toolCount,
    turn.thinkingCount,
    turn.failedCount,
    turn.hasPendingInteraction ? 1 : 0,
    turn.userMessage?.id ?? "",
    items,
    trace,
    subagents,
    artifacts,
    resources,
  ].join("|");
}
