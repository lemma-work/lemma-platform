"use client";

import type { ReactNode, RefObject } from "react";
import { cn } from "@/lib/utils";
import { InlineLoader } from "@/components/brand/loader";
import { Button } from "@/components/ui/button";
import { ArrowDown } from "@/components/ui/icons";
import {
  collectCompletedRunTraceGroups,
  messageHasToolActivity,
  rowIsAfterIndex,
  type DisplayMessageRow,
} from "lemma-sdk";
import type { AssistantControllerView } from "./assistant-types";
import { AssistantMessageViewport } from "./assistant-chrome";
import {
  CompletedRunTraceGroup,
  DisplayResourceCards,
  MessageGroup,
  RunTraceHeader,
  collectDisplayResourceCardsByRow,
} from "./assistant-message-group";
import { ThinkingIndicator } from "./assistant-parts";

type CompletedRunTraceGroups = ReturnType<typeof collectCompletedRunTraceGroups>;
type InlineStatus = { label?: string; shimmer?: boolean } | null | undefined;
type DisplayResourceCardsByRow = ReturnType<typeof collectDisplayResourceCardsByRow>;

export interface AssistantDisplayRowProps {
  row: DisplayMessageRow;
  index: number;
  previousRow: DisplayMessageRow | null;
  controller: AssistantControllerView;
  activeConversationId: string | null;
  displayResourceCardsByRow: DisplayResourceCardsByRow;
  completedRunTraceGroups: CompletedRunTraceGroups;
  inlineRunStatusRowIndex: number;
  inlineRunStatus: InlineStatus;
  isConversationBusy: boolean;
  isRunActive: boolean;
  currentRunLatestUserIndex: number;
  onNavigateResource?: (resourceType: string, resourceId: string, meta?: Record<string, unknown>) => void;
  renderMessageContent: MessageGroupRenderMessageContent;
  renderToolInvocation: MessageGroupRenderToolInvocation;
}

type MessageGroupRenderMessageContent = Parameters<typeof MessageGroup>[0]["renderMessageContent"];
type MessageGroupRenderToolInvocation = Parameters<typeof MessageGroup>[0]["renderToolInvocation"];

export function AssistantDisplayRow({
  row,
  index,
  previousRow,
  controller,
  activeConversationId,
  displayResourceCardsByRow,
  completedRunTraceGroups,
  inlineRunStatusRowIndex,
  inlineRunStatus,
  isConversationBusy,
  isRunActive,
  currentRunLatestUserIndex,
  onNavigateResource,
  renderMessageContent,
  renderToolInvocation,
}: AssistantDisplayRowProps) {
  const includesLastRawMessage = row.sourceIndexes.includes(controller.messages.length - 1);
  const rowHasToolActivity = row.message.role === "assistant" && messageHasToolActivity(row.message);
  const previousRowHasToolActivity = previousRow?.message.role === "assistant" && messageHasToolActivity(previousRow.message);
  const compactAfterAssistant = row.message.role === "assistant"
    && previousRow?.message.role === "assistant"
    && !rowHasToolActivity
    && !previousRowHasToolActivity;
  // The live run trace (consecutive assistant rows in the active run — text,
  // tool steps, thoughts) should read as one tight sequence rather than
  // turn-spaced rows, so tighten it the same way consecutive plain-text rows
  // are. Completed runs fold under "Worked for …" and keep their own spacing.
  const rowInActiveRun = !!isRunActive && row.message.role === "assistant"
    && rowIsAfterIndex(row, currentRunLatestUserIndex);
  const previousRowInActiveRun = !!isRunActive && !!previousRow && previousRow.message.role === "assistant"
    && rowIsAfterIndex(previousRow, currentRunLatestUserIndex);
  const compactActiveRunTrace = rowInActiveRun && previousRowInActiveRun;
  const displayResourceCards = displayResourceCardsByRow.get(index) || [];
  // Rows folded under a "Worked for …" rollup are trace, not the final answer.
  const withinTrace = completedRunTraceGroups.groupedIndexes.has(index);

  return (
    <div key={row.id || index} className={cn((compactAfterAssistant || compactActiveRunTrace) && !withinTrace && "-mt-3")}>
      {index === inlineRunStatusRowIndex ? (
        <div className="mb-3">
          <RunTraceHeader
            label={inlineRunStatus?.label || "Working"}
          />
        </div>
      ) : null}
      <MessageGroup
        message={row.message}
        onNavigateResource={onNavigateResource}
        conversationId={controller.activeConversationId}
        isStreaming={isConversationBusy && includesLastRawMessage && row.message.role === "assistant"}
        isCurrentRunActive={isRunActive && row.message.role === "assistant" && rowIsAfterIndex(row, currentRunLatestUserIndex)}
        withinTrace={withinTrace}
        renderMessageContent={renderMessageContent}
        renderToolInvocation={renderToolInvocation}
        onResolveUserApproval={controller.resolveUserApproval}
      />
      {displayResourceCards.length > 0 ? (
        <div className="mt-2">
          <DisplayResourceCards
            cards={displayResourceCards}
            activeConversationId={activeConversationId}
            onNavigateResource={onNavigateResource}
          />
        </div>
      ) : null}
    </div>
  );
}

export interface AssistantExperienceConversationProps {
  messagesContainerRef: RefObject<HTMLDivElement | null>;
  bottomAnchorRef: RefObject<HTMLDivElement | null>;
  onScroll: () => void;
  contentWidthClassName?: string;
  activeConversationId: string | null;
  resolvedFinalOutput: ReactNode;
  showEmptyState: boolean;
  emptyState: ReactNode;
  isInitialMessageLoading: boolean;
  hasOlderMessages: boolean;
  isLoadingMessages: boolean;
  isLoadingOlderMessages: boolean;
  hasMessages: boolean;
  onLoadOlder: () => void;
  displayMessageRows: DisplayMessageRow[];
  completedRunTraceGroups: CompletedRunTraceGroups;
  renderDisplayRow: (row: DisplayMessageRow, index: number, previousRow: DisplayMessageRow | null) => ReactNode;
  showInlineStatusAtBottom: boolean;
  inlineRunStatus: InlineStatus;
  showInlineToolStatus: boolean;
  inlineToolStatus: InlineStatus;
  showAssistantErrorInTranscript: boolean;
  assistantErrorTitle: string;
  assistantErrorDetails: string;
  showScrollToBottom: boolean;
  onScrollToBottom: () => void;
  isConversationBusy: boolean;
}

export function AssistantExperienceConversation({
  messagesContainerRef,
  bottomAnchorRef,
  onScroll,
  contentWidthClassName,
  activeConversationId,
  resolvedFinalOutput,
  showEmptyState,
  emptyState,
  isInitialMessageLoading,
  hasOlderMessages,
  isLoadingMessages,
  isLoadingOlderMessages,
  hasMessages,
  onLoadOlder,
  displayMessageRows,
  completedRunTraceGroups,
  renderDisplayRow,
  showInlineStatusAtBottom,
  inlineRunStatus,
  showInlineToolStatus,
  inlineToolStatus,
  showAssistantErrorInTranscript,
  assistantErrorTitle,
  assistantErrorDetails,
  showScrollToBottom,
  onScrollToBottom,
  isConversationBusy,
}: AssistantExperienceConversationProps) {
  return (
    <AssistantMessageViewport
      ref={messagesContainerRef}
      onScroll={onScroll}
      innerClassName={contentWidthClassName}
    >
      <div className="flex w-full flex-col gap-5" aria-live="polite" aria-atomic="false">
      {resolvedFinalOutput ? (
        <div>
          {resolvedFinalOutput}
        </div>
      ) : null}

      {showEmptyState ? emptyState : null}

      {isInitialMessageLoading ? (
        <div className="flex items-center justify-center py-10">
          <InlineLoader size="sm" label="Loading messages" className="animate-in fade-in duration-200" />
        </div>
      ) : null}

      {((hasOlderMessages || isLoadingOlderMessages) && hasMessages) ? (
        <div className="flex items-center justify-center py-1">
          <button
            type="button"
            className="rounded-full border border-[var(--border-subtle)] bg-[var(--surface-1)] px-3 py-1.5 text-xs font-medium text-[var(--text-secondary)] transition-colors hover:border-[var(--border-strong)] hover:text-[var(--text-primary)] disabled:cursor-default disabled:opacity-60"
            disabled={!hasOlderMessages || isLoadingMessages || isLoadingOlderMessages}
            onClick={onLoadOlder}
          >
            {isLoadingOlderMessages ? "Loading earlier activity..." : "Load earlier activity"}
          </button>
        </div>
      ) : null}

      <div
        key={activeConversationId || "new-conversation"}
        className={cn(
          "flex w-full flex-col gap-5",
          !isInitialMessageLoading && displayMessageRows.length > 0 && "animate-in fade-in slide-in-from-bottom-1 duration-200",
        )}
      >
      {displayMessageRows.map((row, index) => {
        if (completedRunTraceGroups.groupedIndexes.has(index)) {
          const group = completedRunTraceGroups.groupsByStartIndex.get(index);
          if (!group) return null;

          const groupRows = displayMessageRows.slice(group.startIndex, group.endIndex + 1);
          return (
            <CompletedRunTraceGroup key={`completed-run-${group.startIndex}`} label={group.label}>
              {groupRows.map((groupRow, groupOffset) => {
                const rowIndex = group.startIndex + groupOffset;
                const previousRow = groupOffset > 0 ? groupRows[groupOffset - 1] : null;
                return renderDisplayRow(groupRow, rowIndex, previousRow);
              })}
            </CompletedRunTraceGroup>
          );
        }

        const previousRow = index > 0 ? displayMessageRows[index - 1] : null;
        return renderDisplayRow(row, index, previousRow);
      })}
      </div>

      {showInlineStatusAtBottom ? (
        <div>
          <ThinkingIndicator label={inlineRunStatus?.label} shimmer={inlineRunStatus?.shimmer} />
        </div>
      ) : null}

      {showInlineToolStatus ? (
        <div>
          <ThinkingIndicator label={inlineToolStatus?.label} shimmer={inlineToolStatus?.shimmer} />
        </div>
      ) : null}

      {showAssistantErrorInTranscript ? (
        <div className="state-surface-error rounded-md px-3.5 py-3 text-xs">
          <div className="min-w-0">
            <p className="break-words text-sm font-semibold leading-5">{assistantErrorTitle}</p>
            {assistantErrorDetails && assistantErrorDetails !== assistantErrorTitle ? (
              <pre className="mt-1.5 max-h-40 overflow-auto whitespace-pre-wrap break-words font-mono text-xs leading-5 text-[var(--text-secondary)]">
                {assistantErrorDetails}
              </pre>
            ) : null}
          </div>
        </div>
      ) : null}

      {showScrollToBottom ? (
      <Button
        type="button"
        variant="outline"
        size="icon"
        onClick={onScrollToBottom}
        className="sticky bottom-2 z-10 ml-auto size-8 shadow-md"
        aria-label="Scroll to latest messages"
      >
        <ArrowDown className="size-4" aria-hidden="true" />
      </Button>
      ) : null}
      {(hasMessages || isConversationBusy || showAssistantErrorInTranscript) ? (
        <div aria-hidden="true" className="h-2" />
      ) : null}
      <div ref={bottomAnchorRef} aria-hidden="true" className="h-px" />
      </div>
    </AssistantMessageViewport>
  );
}
