"use client";

// The turn renderer — Direction D: messenger rhythm × document typesetting.
//
// One turn is ask → work → result:
//   - the ask is a right-aligned bubble
//   - the work is a left-aligned status pill ("✓ Worked for 9m 14s · 7 steps",
//     live "● Building · 0:47") whose trace sheet holds every tool row and
//     thought, each tool row still drilling into its full details
//   - speech is speech: narration beats and short answers are left bubbles;
//     a long or structured answer is a doc card with real heading hierarchy
//   - deliverables arrive as artifact cards (video/images/audio play inline)
//   - ask_user and request_approval are in-chat cards where the run paused
//
// Beats, cards and questions all come out of `turn.items` in the order they
// happened — a card never jumps ahead of the question that follows it.
//
// Everything here is fed by the pure turn model in lib/assistant/turns.ts.

import { memo, useState, type ReactNode } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  isAskUserToolName,
  isToolInvocationActive,
  normalizeAssistantMarkdown,
  type AssistantRenderableMessage,
} from "lemma-sdk";
import { cn } from "@/lib/utils";
import { DEFAULT_RESPONDER_NAME } from "@/lib/utils/agents";
import { AssistantAvatar } from "./assistant-avatar";
import { Check, ChevronDown, Copy } from "@/components/ui/icons";
import { InlineLoader } from "@/components/brand/loader";
import { getLemmaClient } from "@/lib/sdk/lemma-client";
import {
  fencedCodeFromPreNode,
  isJsonFenceLanguage,
  parseAssistantJson,
  splitAssistantMessageSegments,
} from "@/lib/assistant/json-blocks";
import { AssistantJsonBlock } from "./assistant-json-block";
import {
  answerIsDocument,
  chatTurnFingerprint,
  completedTurnStatusLabel,
  interactionAnchorId,
  type ChatArtifact,
  type ChatTurn,
  type TraceEntry,
} from "@/lib/assistant/turns";

/** Time-only stamp for cluster tails — day context comes from the daymarks. */
function formatTimeStamp(ms: number | null | undefined): string | null {
  if (typeof ms !== "number" || Number.isNaN(ms)) return null;
  return new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(new Date(ms));
}
import type {
  AssistantMessageRenderArgs,
  AssistantToolRenderArgs,
} from "./assistant-types";
import type { UserApprovalDecision } from "./assistant-experience";
import { formatLiveRunStatus, toolCallLabelParts, type LiveRunStatus } from "./assistant-format";
import { useNowMs } from "./use-assistant-experience";
import { useTurnSettleFlip } from "./use-turn-settle-flip";
import { stripMarkdownNode } from "./assistant-experience-helpers";
import { ToolDetailsPanel } from "./assistant-tool-details";
import { AskUserCard, UserApprovalCard } from "./assistant-approval-cards";
import { AssistantSubagentChipRow } from "./assistant-subagent-chips";
import { DisplayResourceCards } from "./assistant-resource-cards";
import { TRANSCRIPT_ROW_ATTRIBUTE } from "./use-transcript-scroll";

type ToolCardArgs = Record<string, unknown>;
type ToolCardResult = Record<string, unknown> & { success?: boolean; error?: string };

// --- the answer, set as a document ------------------------------------------
//
// The transcript's markdown map flattens headings into small bold paragraphs —
// right for a bubble, wrong for the deliverable-shaped answer that Direction D
// gives a card. This map keeps the same pipeline (GFM, JSON blocks, safe HTML)
// and gives the document a real hierarchy.

const docComponents: Components = {
  p: ({ className, ...props }) => (
    <p className={cn("my-2 leading-6 first:mt-0 last:mb-0", className)} {...stripMarkdownNode(props)} />
  ),
  h1: ({ className, ...props }) => (
    <h2 className={cn("mb-1.5 mt-5 text-lg font-medium leading-snug tracking-[-0.016em] first:mt-0 text-[var(--text-primary)]", className)} {...stripMarkdownNode(props)} />
  ),
  h2: ({ className, ...props }) => (
    <h3 className={cn("mb-1.5 mt-5 text-lg font-medium leading-snug tracking-[-0.016em] first:mt-0 text-[var(--text-primary)]", className)} {...stripMarkdownNode(props)} />
  ),
  h3: ({ className, ...props }) => (
    <h4 className={cn("mb-1.5 mt-4 text-base font-medium leading-snug tracking-[-0.008em] first:mt-0 text-[var(--text-primary)]", className)} {...stripMarkdownNode(props)} />
  ),
  h4: ({ className, ...props }) => (
    <h5 className={cn("mb-1 mt-3 text-sm font-semibold first:mt-0 text-[var(--text-primary)]", className)} {...stripMarkdownNode(props)} />
  ),
  ul: ({ className, ...props }) => (
    <ul className={cn("lchat-doc-list my-2 space-y-1 first:mt-0 last:mb-0", className)} {...stripMarkdownNode(props)} />
  ),
  ol: ({ className, ...props }) => (
    <ol className={cn("my-2 list-decimal space-y-1 pl-5 first:mt-0 last:mb-0", className)} {...stripMarkdownNode(props)} />
  ),
  li: ({ className, ...props }) => (
    <li className={cn("pl-1 leading-6", className)} {...stripMarkdownNode(props)} />
  ),
  strong: ({ className, ...props }) => (
    <strong className={cn("font-semibold text-[var(--text-primary)]", className)} {...stripMarkdownNode(props)} />
  ),
  em: ({ className, ...props }) => (
    <em className={cn("text-[var(--text-secondary)]", className)} {...stripMarkdownNode(props)} />
  ),
  blockquote: ({ className, ...props }) => (
    <blockquote className={cn("my-2 border-l-2 border-[var(--border-strong)] pl-4 text-[var(--text-secondary)] first:mt-0 last:mb-0", className)} {...stripMarkdownNode(props)} />
  ),
  code: ({ className, ...props }) => (
    <code className={cn("lchat-code", className)} {...stripMarkdownNode(props)} />
  ),
  // Same JSON-fence detection as the transcript map, so a payload inside a
  // document still gets the structured block instead of raw text.
  pre: ({ className, node, ...props }) => {
    const fenced = fencedCodeFromPreNode(node);
    const json = fenced && (fenced.language === null || isJsonFenceLanguage(fenced.language))
      ? parseAssistantJson(fenced.text)
      : null;
    if (json) return <AssistantJsonBlock json={json} isUserMessage={false} />;
    return (
      <pre className={cn("my-2.5 overflow-x-auto rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-2)] p-3 text-xs first:mt-0 last:mb-0", className)} {...props} />
    );
  },
  table: ({ className, ...props }) => (
    <div className="my-2.5 w-full overflow-x-auto rounded-lg border border-[var(--border-subtle)] first:mt-0 last:mb-0">
      <table className={cn("w-full min-w-max border-collapse text-sm", className)} {...stripMarkdownNode(props)} />
    </div>
  ),
  th: ({ className, ...props }) => (
    <th className={cn("border-b border-[var(--border-subtle)] bg-[var(--surface-2)] px-3 py-2 text-left font-medium text-[var(--text-secondary)]", className)} {...stripMarkdownNode(props)} />
  ),
  td: ({ className, ...props }) => (
    <td className={cn("border-b border-[var(--border-subtle)] px-3 py-2 align-top last:border-b-0", className)} {...stripMarkdownNode(props)} />
  ),
  a: ({ className, target, rel, ...props }) => (
    <a
      {...stripMarkdownNode(props)}
      className={cn("font-medium text-[var(--action-primary)] underline-offset-4 hover:underline", className)}
      target={target || "_blank"}
      rel={rel || "noreferrer noopener"}
    />
  ),
  hr: () => <hr className="my-5 border-[var(--border-subtle)]" />,
};

// Memoized: the doc card's markdown is the most expensive parse in the
// transcript, and a streaming turn re-renders every flush. While the answer
// grows it re-parses (the text changes); once it settles, it never does again.
export const AnswerDocument = memo(function AnswerDocument({ text }: { text: string }) {
  const segments = splitAssistantMessageSegments(text);
  return (
    <div className="min-w-0 overflow-hidden break-words text-sm leading-6 text-[var(--text-primary)]">
      {segments.map((segment, index) => (
        segment.kind === "json" ? (
          <AssistantJsonBlock key={`json-${index}`} json={segment.json} isUserMessage={false} />
        ) : (
          <ReactMarkdown
            key={`markdown-${index}`}
            remarkPlugins={[remarkGfm]}
            skipHtml
            components={docComponents}
          >
            {normalizeAssistantMarkdown(segment.text)}
          </ReactMarkdown>
        )
      ))}
    </div>
  );
});

// Speech, memoized on what it says. A re-render of the live turn (every
// streaming flush) re-runs the renderer only for the beats whose text, role,
// or id actually changed — the settled bubbles above the frontier keep their
// markdown out of the parse.
const SpeechContent = memo(function SpeechContent({
  message,
  render,
}: {
  message: AssistantRenderableMessage;
  render: (args: AssistantMessageRenderArgs) => ReactNode;
}) {
  return <>{render({ message })}</>;
}, (prev, next) =>
  prev.render === next.render
  && prev.message.id === next.message.id
  && prev.message.role === next.message.role
  && prev.message.content === next.message.content
);

// --- copy: a property of every bubble, not a bar reserving space ------------
//
// One floating button that appears on the bubble's hover and overlays its top
// edge — so when it is hidden it costs no layout, and the card below a bubble
// is never pushed down by an invisible affordance.

function HoverCopyButton({ text, side }: { text: string; side: "left" | "right" }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      aria-label="Copy message"
      title={copied ? "Copied" : "Copy"}
      className={cn(
        "lchat-copybtn",
        side === "left" ? "lchat-copybtn-left" : "lchat-copybtn-right",
      )}
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setCopied(true);
          setTimeout(() => setCopied(false), 1600);
        } catch { /* clipboard denied */ }
      }}
    >
      {copied ? <Check className="size-3 text-[var(--state-success)]" /> : <Copy className="size-3" />}
    </button>
  );
}

// --- status pill + trace sheet ------------------------------------------------

function TraceThinkingRow({ entry }: { entry: Extract<TraceEntry, { kind: "thinking" }> }) {
  // One italic line in the rail; the full monologue is a click away.
  const [isExpanded, setIsExpanded] = useState(false);
  return (
    <div className="lchat-tr-step lchat-tr-thought">
      <button
        type="button"
        className="lchat-tr-thought-line"
        data-expanded={isExpanded || undefined}
        onClick={() => setIsExpanded((prev) => !prev)}
        title={isExpanded ? "Collapse" : "Read the full thought"}
      >
        <span className="lchat-tr-thought-label">Thought</span>
        <span className="lchat-tr-thought-text">{entry.text}</span>
      </button>
    </div>
  );
}

function TraceToolRow({
  entry,
  isSelected,
  onSelect,
  onNavigateResource,
  onResolveUserApproval,
  renderToolInvocation,
  activeConversationId,
}: {
  entry: Extract<TraceEntry, { kind: "tool" }>;
  isSelected: boolean;
  onSelect: () => void;
  onNavigateResource?: (resourceType: string, resourceId: string, meta?: Record<string, unknown>) => void;
  onResolveUserApproval?: (approvalId: string, decision: UserApprovalDecision, response?: Record<string, unknown> | null) => Promise<void>;
  renderToolInvocation?: (args: AssistantToolRenderArgs) => ReactNode;
  activeConversationId: string | null;
}) {
  const invocation = entry.invocation;
  const resultData = (invocation.result || {}) as ToolCardResult;
  const isExecuting = isToolInvocationActive(invocation);
  const isFailed = !isExecuting && invocation.state === "result" && resultData.success === false;
  const { verb, object } = toolCallLabelParts(invocation.toolName, invocation.args as ToolCardArgs);

  return (
    <div className="lchat-tr-step" data-retry={isFailed || undefined}>
      <button
        type="button"
        className="lchat-tr-call"
        data-state={isExecuting ? "executing" : isFailed ? "failed" : "complete"}
        data-selected={isSelected || undefined}
        onClick={onSelect}
      >
        {verb ? <span className="lchat-tr-verb">{verb}</span> : null}
        <span className="lchat-tr-object">{object}</span>
        {isFailed ? <span className="lchat-tr-failed" title="Tool failed">!</span> : null}
      </button>
      {isSelected ? (
        <div className="lchat-tr-detail">
          <ToolDetailsPanel
            toolCallId={invocation.toolCallId}
            toolName={invocation.toolName}
            args={invocation.args as ToolCardArgs}
            state={invocation.state}
            result={invocation.result as ToolCardResult | undefined}
            onNavigateResource={onNavigateResource}
            onResolveUserApproval={onResolveUserApproval}
            renderToolInvocation={renderToolInvocation}
            message={entry.message}
            activeConversationId={activeConversationId}
          />
        </div>
      ) : null}
    </div>
  );
}

export function TurnStatusPill({
  turn,
  liveToolLabel,
  liveRunStatus,
  activeConversationId,
  onNavigateResource,
  onResolveUserApproval,
  renderToolInvocation,
}: {
  turn: ChatTurn;
  liveToolLabel?: string | null;
  liveRunStatus?: LiveRunStatus | null;
  activeConversationId: string | null;
  onNavigateResource?: (resourceType: string, resourceId: string, meta?: Record<string, unknown>) => void;
  onResolveUserApproval?: (approvalId: string, decision: UserApprovalDecision, response?: Record<string, unknown> | null) => Promise<void>;
  renderToolInvocation?: (args: AssistantToolRenderArgs) => ReactNode;
}) {
  const [isOpen, setIsOpen] = useState(false);
  const [activeDetailId, setActiveDetailId] = useState<string | null>(null);
  // The live label's elapsed second ticks here — one pill re-renders, not the
  // transcript. A settled pill reads its duration off the turn itself
  // and never ticks.
  const nowMs = useNowMs(turn.isLive);
  const label = turn.isLive
    ? (liveToolLabel || (liveRunStatus ? formatLiveRunStatus(liveRunStatus, nowMs).label : null) || "Working")
    : completedTurnStatusLabel(turn);
  const canExpand = turn.trace.length > 0;

  if (!label) return null;

  return (
    <div className="lchat-status">
      <button
        type="button"
        className="lchat-pill"
        data-live={turn.isLive || undefined}
        disabled={!canExpand}
        onClick={() => setIsOpen((prev) => !prev)}
        aria-expanded={canExpand ? isOpen : undefined}
      >
        {turn.isLive ? (
          <span className="lchat-pulse" aria-hidden="true" />
        ) : (
          <span className="lchat-pill-check-wrap" aria-hidden="true">
            <Check className="lchat-pill-check" />
          </span>
        )}
        <span className="lchat-pill-label">{label}</span>
        {canExpand ? (
          <ChevronDown className={cn("lchat-pill-chev", isOpen && "rotate-180")} aria-hidden="true" />
        ) : null}
      </button>

      {isOpen && canExpand ? (
        <div className="lchat-trace">
          {turn.trace.map((entry) => (
            entry.kind === "thinking" ? (
              <TraceThinkingRow key={entry.id} entry={entry} />
            ) : (
              <TraceToolRow
                key={entry.id}
                entry={entry}
                isSelected={activeDetailId === entry.invocation.toolCallId}
                onSelect={() => setActiveDetailId((prev) => (
                  prev === entry.invocation.toolCallId ? null : entry.invocation.toolCallId
                ))}
                onNavigateResource={onNavigateResource}
                onResolveUserApproval={onResolveUserApproval}
                renderToolInvocation={renderToolInvocation}
                activeConversationId={activeConversationId}
              />
            )
          ))}
        </div>
      ) : null}
    </div>
  );
}

// --- artifact cards -----------------------------------------------------------

function formatBytes(sizeBytes?: number): string | null {
  if (typeof sizeBytes !== "number" || !Number.isFinite(sizeBytes) || sizeBytes < 0) return null;
  if (sizeBytes < 1024) return `${sizeBytes} B`;
  if (sizeBytes < 1024 * 1024) return `${Math.round(sizeBytes / 1024)} KB`;
  return `${(sizeBytes / (1024 * 1024)).toFixed(1)} MB`;
}

function compactPath(path: string): string {
  const parts = path.replace(/^\/+/, "").split("/").filter(Boolean);
  if (parts.length <= 2) return parts.join("/");
  return `${parts[0]}/…/${parts[parts.length - 1]}`;
}

function FileArtifactCard({ artifact, onOpen }: { artifact: ChatArtifact; onOpen?: () => void }) {
  const meta = [formatBytes(artifact.sizeBytes), compactPath(artifact.path)].filter(Boolean).join(" · ");
  const body = (
    <>
      <span className="lchat-file-ic" data-ext={artifact.ext.toLowerCase()} aria-hidden="true">{artifact.ext}</span>
      <span className="lchat-file-text">
        <span className="lchat-file-n">{artifact.name}</span>
        <span className="lchat-file-m">{meta || artifact.fileName}</span>
      </span>
      <span className="lchat-file-go">Open</span>
    </>
  );
  const className = "lchat-file";
  // The in-conversation presentation stage is the handler; a bare files-route
  // link is the fallback for surfaces without one (never yank the reader out
  // of the conversation when the stage can host the file beside it).
  if (onOpen) {
    return <button type="button" onClick={onOpen} className={cn(className, "cursor-pointer text-left")}>{body}</button>;
  }
  return artifact.href
    ? <Link href={artifact.href} className={className}>{body}</Link>
    : <div className={className}>{body}</div>;
}

/** Video, image and audio artifacts fetch a short-lived file URL and play
 * inline, the way a messenger renders media. Anything that fails degrades to
 * the plain file card. */
function MediaArtifactCard({ artifact, podId, onOpen }: { artifact: ChatArtifact; podId: string | null; onOpen?: () => void }) {
  const urlQuery = useQuery({
    queryKey: ["chat-artifact-url", podId, artifact.path],
    queryFn: async () => {
      const response = await getLemmaClient(podId as string).files.getUrl(artifact.path);
      return response.url as string;
    },
    enabled: Boolean(podId),
    staleTime: 5 * 60_000,
    retry: 1,
  });

  if (!podId || urlQuery.isError) return <FileArtifactCard artifact={artifact} onOpen={onOpen} />;

  const meta = [formatBytes(artifact.sizeBytes), compactPath(artifact.path)].filter(Boolean).join(" · ");
  const openControl = onOpen ? (
    <button type="button" onClick={onOpen} className="lchat-file-go">Open</button>
  ) : artifact.href ? (
    <Link href={artifact.href} className="lchat-file-go">Open</Link>
  ) : null;
  // Audio has no frame to fill: it is a player strip, not a stage, so it keeps
  // the card's own background instead of the video stock.
  if (artifact.kind === "audio") {
    return (
      <div className="lchat-media">
        <div className="lchat-audio">
          {!urlQuery.data
            ? <InlineLoader size="xs" />
            : <audio className="lchat-audio-player" controls preload="metadata" src={urlQuery.data} />}
        </div>
        <div className="lchat-media-meta">
          <span className="lchat-file-n">{artifact.name}</span>
          {meta ? <span className="lchat-file-m">{meta}</span> : null}
          {openControl}
        </div>
      </div>
    );
  }

  return (
    <div className="lchat-media">
      <div className={artifact.kind === "video" ? "lchat-media-stage lchat-media-stage-video" : "lchat-media-stage"}>
        {!urlQuery.data ? (
          <div className="lchat-media-loading"><InlineLoader size="xs" /></div>
        ) : artifact.kind === "video" ? (
          <video className="lchat-video-player" controls preload="metadata" src={urlQuery.data} />
        ) : (
          // eslint-disable-next-line @next/next/no-img-element -- signed pod file URL; next/image cannot proxy it
          <img className="lchat-image" src={urlQuery.data} alt={artifact.name} />
        )}
      </div>
      <div className="lchat-media-meta">
        <span className="lchat-file-n">{artifact.fileName}</span>
        {meta ? <span className="lchat-file-m">{meta}</span> : null}
        {openControl}
      </div>
    </div>
  );
}

// --- the turn -----------------------------------------------------------------

export interface AssistantTurnViewProps {
  turn: ChatTurn;
  activeConversationId: string | null;
  podId: string | null;
  /** Live-only inputs, null for settled turns: the streaming tool's label and
   *  the clockless run-status model the pill formats against its own tick. */
  liveToolLabel?: string | null;
  liveRunStatus?: LiveRunStatus | null;
  onNavigateResource?: (resourceType: string, resourceId: string, meta?: Record<string, unknown>) => void;
  onResolveUserApproval?: (approvalId: string, decision: UserApprovalDecision, response?: Record<string, unknown> | null) => Promise<void>;
  renderMessageContent: (args: AssistantMessageRenderArgs) => ReactNode;
  renderToolInvocation?: (args: AssistantToolRenderArgs) => ReactNode;
  /**
   * What to call the person who sent a turn. Absent while the conversation
   * holds one voice, which is what keeps a plain one-to-one chat unlabelled.
   */
  resolveSenderName?: (userId: string) => string | null;
  /** What to call the agent that answered a turn. Absent for the same reason. */
  resolveAgentName?: (agentId: string) => string | null;
}

// Memoized against the turn fingerprint: `buildChatTurns` rebuilds every turn
// object on every streaming flush, so identity says nothing — the fingerprint
// says everything. While one turn streams, the rest of the history holds still.
export const AssistantTurnView = memo(function AssistantTurnView({
  turn,
  activeConversationId,
  podId,
  liveToolLabel,
  liveRunStatus,
  onNavigateResource,
  onResolveUserApproval,
  renderMessageContent,
  renderToolInvocation,
  resolveSenderName,
  resolveAgentName,
}: AssistantTurnViewProps) {
  const userTimestamp = turn.userMessage?.createdAt ? formatTimeStamp(turn.userMessage.createdAt.getTime()) : null;
  // Everybody's own name, including the reader's. It was "You" for your own
  // messages, which is how a messenger does it and was wrong here: with two
  // windows open it is genuinely ambiguous whose "You" you are looking at, and
  // a name is never ambiguous. Slack shows you your own name for this reason.
  //
  // Both resolvers are absent while the conversation holds a single voice, so
  // a plain one-to-one chat renders exactly as it did.
  const senderName = turn.senderUserId && resolveSenderName
    ? resolveSenderName(turn.senderUserId)
    : null;
  // The pod assistant has no agent row, so a turn it answered carries no id,
  // and the fallback names it rather than leaving a blank where every other
  // turn has a name.
  //
  // But not while the turn is still streaming and unattributed: the bubble is
  // built client-side from token deltas, before any frame says who is
  // answering, so falling back there labelled every live reply as the default
  // agent — batman's answers included. No name for a moment is honest; the
  // wrong name is not.
  const agentUnknownWhileLive = turn.isLive && !turn.agentId;
  const agentName = resolveAgentName && turn.items.length > 0 && !agentUnknownWhileLive
    ? (turn.agentId ? resolveAgentName(turn.agentId) : DEFAULT_RESPONDER_NAME)
    : null;
  const showStatusPill = turn.isLive || turn.trace.length > 0;
  // The assistant's stamp rides under the turn's last beat, not every bubble.
  const assistantStamp = !turn.isLive && turn.items.length > 0 ? formatTimeStamp(turn.endedAtMs) : null;
  // A turn that was live and just settled earns one settle motion — its pill
  // crossing from typing indicator to summary. Turns loaded from history were
  // never live, so they mount without it (arrival motion is for arrivals).
  // "Was live" is a fact about the turn's past that the model does not carry,
  // tracked with the adjust-state-during-render pattern: one extra render,
  // once per turn, the first time it renders live.
  const [seenLive, setSeenLive] = useState(false);
  // Read once, at the mount: a turn that was live when it appeared is an
  // arrival and animates in; history was never live and mounts without motion.
  const [arrivedLive] = useState(() => turn.isLive);
  if (turn.isLive && !seenLive) setSeenLive(true);
  const isSettled = seenLive && !turn.isLive;
  // The settle reorganizes the turn in one commit (pill to the header slot,
  // beats shifting down); the FLIP turns that snap into a slide.
  const settleFlipRef = useTurnSettleFlip(turn.isLive, isSettled);
  const statusPill = showStatusPill ? (
    <TurnStatusPill
      turn={turn}
      liveToolLabel={liveToolLabel}
      liveRunStatus={liveRunStatus}
      activeConversationId={activeConversationId}
      onNavigateResource={onNavigateResource}
      onResolveUserApproval={onResolveUserApproval}
      renderToolInvocation={renderToolInvocation}
    />
  ) : null;

  const speechContent = (text: string, message: AssistantRenderableMessage | null, role: "user" | "assistant", speechId: string) => (
    <SpeechContent
      message={{
        ...(message ?? { id: speechId, role, content: "" }),
        role,
        content: text,
        parts: undefined,
        toolInvocations: undefined,
      } as AssistantRenderableMessage}
      render={renderMessageContent}
    />
  );

  // `data-arrived` drives the entrance motion and is fixed at the mount, never
  // moving again. `data-live` cannot carry it: it flips on a few hundred
  // milliseconds after the turn is already painted, and a CSS rule that starts
  // matching an element already on screen replays its animation there — which
  // is exactly how a sent message came to flicker once, on every send.
  return (
    <div
      ref={settleFlipRef}
      className="lchat-turn"
      data-live={turn.isLive || undefined}
      data-arrived={arrivedLive || undefined}
      data-settled={isSettled || undefined}
      {...{ [TRANSCRIPT_ROW_ATTRIBUTE]: ""}}
    >
      {turn.userMessage && turn.userMessage.content.trim() ? (
        <div className="lchat-user">
          {senderName ? (
            <div className="lchat-user-sender">
              <AssistantAvatar name={senderName} seed={turn.senderUserId} />
              <span>{senderName}</span>
            </div>
          ) : null}
          <div className="lchat-bubble lchat-bubble-user group relative">
            {speechContent(turn.userMessage.content, turn.userMessage, "user", turn.userMessage.id)}
            <HoverCopyButton text={turn.userMessage.content} side="left" />
          </div>
          {userTimestamp ? (
            <time className="lchat-user-time" dateTime={turn.userMessage?.createdAt?.toISOString()}>{userTimestamp}</time>
          ) : null}
        </div>
      ) : null}

      {/* Completed: the pill is the turn's summary header, up top.
          Live: it is the typing indicator — it rides the frontier, after the
          newest beat, so "what it is doing right now" is never stranded above
          the bubbles it is producing. */}
      {agentName ? (
        <div className="lchat-agent-sender">
          <AssistantAvatar name={agentName} seed={turn.agentId} />
          <span>{agentName}</span>
        </div>
      ) : null}

      {/* In flight and nothing said yet. A blank turn cannot be told apart
          from a dead one, and the status pill only appears once there is work
          to describe. */}
      {turn.isLive && turn.items.length === 0 ? (
        <div className="lchat-typing" aria-label="typing">
          <span /><span /><span />
        </div>
      ) : null}

      {/* Delivered, and nobody picked it up. Said plainly: an unanswered turn
          and a broken one look the same otherwise, and the reader cannot tell
          which they are looking at. */}
      {turn.unanswered ? (
        <div className="lchat-unanswered">No agent replied to this</div>
      ) : null}

      {!turn.isLive ? statusPill : null}

      {/* Something happened here that this reader may not see. Said plainly,
          rather than leaving a turn that jumps from a question to an answer
          with no sign that any work was done in between. */}
      {turn.traceWithheld ? (
        <div className="lchat-trace-withheld">Worked privately</div>
      ) : null}

      {turn.items.map((item) => {
        if (item.kind === "notice") {
          return <div key={item.id} className="lchat-notice">{item.text}</div>;
        }

        if (item.kind === "artifact") {
          const artifact = item.artifact;
          // Open beside the conversation in the presentation stage — the
          // same handler the display_resource cards have always used.
          const openInStage = onNavigateResource ? () => onNavigateResource("display_resource", artifact.toolCallId ?? artifact.key, {
            request: { type: "FILE", path: artifact.path, loadingMessages: [] },
            conversationId: activeConversationId,
          }) : undefined;
          return (
            <div key={item.id} className="lchat-atts">
              {artifact.kind === "file"
                ? <FileArtifactCard artifact={artifact} onOpen={openInStage} />
                : <MediaArtifactCard artifact={artifact} podId={podId} onOpen={openInStage} />}
            </div>
          );
        }

        if (item.kind === "resource") {
          return (
            <div key={item.id} className="lchat-resources">
              <DisplayResourceCards
                cards={[item.card]}
                activeConversationId={activeConversationId}
                onNavigateResource={onNavigateResource}
              />
            </div>
          );
        }

        if (item.kind === "interaction") {
          const isAsk = isAskUserToolName(item.invocation.toolName);
          return (
            <div
              key={item.id}
              className="lchat-interaction"
              id={interactionAnchorId(item.invocation.toolCallId)}
            >
              {isAsk ? (
                <AskUserCard
                  invocation={item.invocation}
                  onResolveUserApproval={onResolveUserApproval}
                />
              ) : (
                <UserApprovalCard
                  invocation={item.invocation}
                  onResolveUserApproval={onResolveUserApproval}
                />
              )}
            </div>
          );
        }

        // Speech: narration and short answers are bubbles; the turn's closing
        // answer is a document card when it is long or structured. Both rules
        // are pure functions of the text, so a streaming answer crosses into a
        // card without a layout surprise. There is no in-bubble caret — the
        // streaming text growing *is* the activity, and the live pill says it.
        if (!item.text.trim()) return null;
        if (item.answer && item.documentEligible && answerIsDocument(item.text)) {
          return (
            <div key={item.id} className="lchat-doc group relative">
              <AnswerDocument text={item.text} />
              {!turn.isLive ? <HoverCopyButton text={item.text} side="right" /> : null}
            </div>
          );
        }
        return (
          <div key={item.id} className={cn("lchat-bubble lchat-bubble-them group relative", !item.answer && "lchat-bubble-narr")}>
            {speechContent(item.text, null, "assistant", item.id)}
            {!item.streaming ? <HoverCopyButton text={item.text} side="right" /> : null}
          </div>
        );
      })}

      {/* The sub-agents this turn delegated to, once they have settled — the
          record of the delegation, sitting where the delegation happened.
          Live ones are absent by design: they are in the dock above the
          composer, because a sub-agent outlives the turn that spawned it and
          this turn will have scrolled away long before it finishes. */}
      {turn.subagentParts.length > 0 ? (
        <AssistantSubagentChipRow
          parts={turn.subagentParts}
          parentConversationId={activeConversationId}
          isRunActive={turn.isLive}
        />
      ) : null}

      {turn.isLive ? statusPill : null}

      {assistantStamp ? <div className="lchat-stamp">{assistantStamp}</div> : null}
    </div>
  );
}, (prev, next) =>
  chatTurnFingerprint(prev.turn) === chatTurnFingerprint(next.turn)
  && prev.activeConversationId === next.activeConversationId
  && prev.podId === next.podId
  && prev.liveToolLabel === next.liveToolLabel
  && prev.liveRunStatus === next.liveRunStatus
  && prev.onNavigateResource === next.onNavigateResource
  && prev.onResolveUserApproval === next.onResolveUserApproval
  && prev.renderMessageContent === next.renderMessageContent
  && prev.renderToolInvocation === next.renderToolInvocation
);
