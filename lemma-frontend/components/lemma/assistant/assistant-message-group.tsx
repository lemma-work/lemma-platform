"use client";

// Per-message rendering for the assistant transcript: the completed run-trace
// group wrapper and the <MessageGroup> that lays out a single message's text,
// reasoning, tool rollups and final answer. The tool-activity rollup, inline
// tool/approval calls, user-approval cards and display-resource cards now live in
// sibling modules; their previously-exported symbols are re-exported here so the
// original import surface is unchanged. No runtime cycle with assistant-experience
// — only its TYPES are imported here.

import { useState, type ReactNode } from "react";
import {
  isFinalResultToolName,
  messageTextContent,
  normalizeAssistantDisplayText,
} from "lemma-sdk";
import { formatMessageTimestamp, normalizeToolParts } from "./assistant-format";
import { ReasoningPartCard } from "./assistant-parts";
import {
  RunTraceHeader,
  TextBlockWithCopy,
  ToolActivityRollup,
} from "./assistant-tool-details";
import type {
  AssistantMessagePart,
  AssistantRenderableMessage,
} from "lemma-sdk/react";
import type {
  AssistantMessageRenderArgs,
  AssistantToolRenderArgs,
} from "./assistant-types";
import type { UserApprovalDecision } from "./assistant-experience";

// Re-export the message-rendering pieces moved to sibling modules so existing
// importers of assistant-message-group keep working unchanged.
export {
  collectDisplayResourceCardsByRow,
  currentPodIdFromBrowserPath,
  DisplayResourceCards,
} from "./assistant-resource-cards";
export { ComposerApprovalPanel, ComposerAskUserPanel } from "./assistant-approval-cards";
export { pluralize, RunTraceHeader } from "./assistant-tool-details";

export function CompletedRunTraceGroup({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  const [isExpanded, setIsExpanded] = useState(false);

  return (
    <div className="flex min-w-0 flex-col gap-2">
      <RunTraceHeader
        label={label}
        isExpanded={isExpanded}
        isInteractive
        onToggle={() => setIsExpanded((prev) => !prev)}
      />
      {isExpanded ? (
        <div className="flex min-w-0 flex-col gap-3">
          {children}
        </div>
      ) : null}
    </div>
  );
}

export function MessageGroup({
  message,
  conversationId,
  onNavigateResource,
  isStreaming,
  isCurrentRunActive,
  withinTrace,
  renderMessageContent,
  renderToolInvocation,
  onResolveUserApproval,
}: {
  message: AssistantRenderableMessage;
  conversationId?: string | null;
  onNavigateResource?: (resourceType: string, resourceId: string, meta?: Record<string, unknown>) => void;
  isStreaming: boolean;
  isCurrentRunActive?: boolean;
  /** This message is part of a run trace (folded under "Worked for …"), not the
   * final answer — so its text blocks drop the copy/timestamp affordance. */
  withinTrace?: boolean;
  renderMessageContent: (args: AssistantMessageRenderArgs) => ReactNode;
  renderToolInvocation?: (args: AssistantToolRenderArgs) => ReactNode;
  onResolveUserApproval?: (approvalId: string, decision: UserApprovalDecision, response?: Record<string, unknown> | null) => Promise<void>;
}) {
  type ToolPart = Extract<AssistantMessagePart, { type: "tool" }>;
  type ReasoningPart = Extract<AssistantMessagePart, { type: "reasoning" }>;
  type NonToolPart = Exclude<AssistantMessagePart, { type: "tool" }>;
  type MessageRenderBlock =
    | { id: string; kind: "content"; part: NonToolPart }
    | { id: string; kind: "tools"; toolParts: ToolPart[] };

  const fallbackText = normalizeAssistantDisplayText(typeof message.content === "string" ? message.content : messageTextContent(message));
  const orderedParts: AssistantMessagePart[] = normalizeToolParts(message.parts && message.parts.length > 0
    ? message.parts
    : [
      ...(fallbackText
        ? [{ id: `${message.id}-fallback-text`, type: "text", text: fallbackText } as AssistantMessagePart]
        : []),
      ...((message.toolInvocations || []).map((tool, index) => ({
        id: `${tool.toolCallId || message.id}-fallback-tool-${index}`,
        type: "tool",
        toolInvocation: tool,
      } as AssistantMessagePart))),
    ]);

  const toolParts = orderedParts.filter((part): part is ToolPart => part.type === "tool");
  const groupedToolParts = toolParts.filter((part) => !isFinalResultToolName(part.toolInvocation.toolName));
  const reasoningParts = orderedParts.filter((part): part is ReasoningPart => part.type === "reasoning");
  const rollupOrderedParts = orderedParts.filter((part): part is ToolPart | ReasoningPart => (
    part.type === "reasoning" || (part.type === "tool" && !isFinalResultToolName(part.toolInvocation.toolName))
  ));
  const blocks: MessageRenderBlock[] = [];

  orderedParts.forEach((part) => {
    if (part.type === "tool") {
      if (isFinalResultToolName(part.toolInvocation.toolName)) {
        return;
      }

      const lastBlock = blocks[blocks.length - 1];
      if (lastBlock?.kind === "tools") {
        lastBlock.toolParts.push(part);
      } else {
        blocks.push({
          id: `${part.id}-tools`,
          kind: "tools",
          toolParts: [part],
        });
      }
      return;
    }

    blocks.push({
      id: part.id,
      kind: "content",
      part,
    });
  });

  void isStreaming;
  const firstToolsBlockId = blocks.find((block) => block.kind === "tools")?.id;
  const hasTextParts = orderedParts.some((part) => part.type === "text" && part.text.trim().length > 0);
  const foldReasoningIntoToolRollup = groupedToolParts.length > 0 && reasoningParts.length > 0 && !hasTextParts;
  const foldReasoningIntoRunRollup = false;
  const rollupRunStatusLabel: string | undefined = undefined;
  const messageTimestamp = formatMessageTimestamp(message.createdAt);
  const lastTextBlockIndex = blocks.reduce((last, b, i) =>
    b.kind === "content" && b.part.type === "text" ? i : last, -1);

  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="flex flex-col items-end gap-1">
          <div className="lemma-assistant-user-message-bubble ml-auto max-w-prose bg-[var(--surface-2)] px-3 py-2 text-[var(--text-primary)] shadow-[var(--shadow-xs)]">
            {renderMessageContent({
              message: {
                ...message,
                content: message.content,
                parts: undefined,
                toolInvocations: undefined,
              },
            })}
          </div>
          {messageTimestamp ? (
            <time
              className="text-xs text-[var(--text-secondary)]"
              dateTime={messageTimestamp.dateTime}
            >
              {messageTimestamp.text}
            </time>
          ) : null}
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-col gap-2">
        {foldReasoningIntoRunRollup ? (
          <ToolActivityRollup
            key={`${message.id}-run-trace`}
            collapsedLabel={rollupRunStatusLabel}
            isRunActive={isCurrentRunActive}
            detailParts={reasoningParts}
            onNavigateResource={onNavigateResource}
            onResolveUserApproval={onResolveUserApproval}
            renderToolInvocation={renderToolInvocation}
            message={message}
            activeConversationId={conversationId ?? null}
          />
        ) : null}
        {blocks.map((block, blockIndex) => {
          if (block.kind === "tools") {
            if (foldReasoningIntoToolRollup && block.id !== firstToolsBlockId) {
              return null;
            }

            return (
                <ToolActivityRollup
                  key={block.id}
                  collapsedLabel={block.id === firstToolsBlockId ? rollupRunStatusLabel : undefined}
                  isRunActive={isCurrentRunActive}
                  detailParts={
                  foldReasoningIntoToolRollup && block.id === firstToolsBlockId
                    ? rollupOrderedParts
                    : block.toolParts
                }
                onNavigateResource={onNavigateResource}
                onResolveUserApproval={onResolveUserApproval}
                renderToolInvocation={renderToolInvocation}
                message={message}
                activeConversationId={conversationId ?? null}
              />
            );
          }

          const part = block.part;
          if (part.type === "text") {
            const trimmedText = normalizeAssistantDisplayText(part.text);
            if (trimmedText.length === 0) {
              return null;
            }

            const contentNode = renderMessageContent({
              message: {
                ...message,
                content: trimmedText,
                parts: undefined,
                toolInvocations: undefined,
              },
            });

            if (message.role === "user") {
              return <div key={part.id}>{contentNode}</div>;
            }

            return (
              <TextBlockWithCopy
                key={part.id}
                text={trimmedText}
                timestamp={blockIndex === lastTextBlockIndex ? messageTimestamp : null}
                // While the run is live, drop the hover copy/timestamp bar: its
                // reserved height shows up as inconsistent gaps between streaming
                // blocks. It returns once the run settles.
                showActions={!withinTrace && !isCurrentRunActive}
              >
                {contentNode}
              </TextBlockWithCopy>
            );
          }

          if (part.type === "reasoning") {
            if (foldReasoningIntoToolRollup || foldReasoningIntoRunRollup) {
              return null;
            }

            return (
              <ReasoningPartCard
                key={part.id}
                text={part.text}
                isStreaming={part.state === "streaming"}
                durationMs={part.durationMs}
              />
            );
          }

          return null;
        })}

      </div>
    </div>
  );
}
