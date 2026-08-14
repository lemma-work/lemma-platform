"use client";

import type { ReactNode, RefObject } from "react";
import { cn } from "@/lib/utils";
import { useLoadingGate } from "@/components/shared/loading";
import { InlineLoader } from "@/components/brand/loader";
import { Button } from "@/components/ui/button";
import { ArrowDown, RefreshCw, RotateCcw } from "@/components/ui/icons";
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

/** How long a transcript stays silently empty before it admits it is fetching. */
const TRANSCRIPT_WAIT_DELAY_MS = 600;

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
  // A run's trace (consecutive assistant rows — text, tool steps, thoughts)
  // reads as one tight sequence rather than turn-spaced rows.
  //
  // Deliberately not a function of `isRunActive`: keying spacing off whether the
  // run happens to be live meant the same transcript was tight while you watched
  // it and loose after a reload. What a row is does not change; how far it sits
  // from its neighbour should not either.
  const rowInLatestRun = row.message.role === "assistant"
    && rowIsAfterIndex(row, currentRunLatestUserIndex);
  const previousRowInLatestRun = !!previousRow && previousRow.message.role === "assistant"
    && rowIsAfterIndex(previousRow, currentRunLatestUserIndex);
  const compactActiveRunTrace = rowInLatestRun && previousRowInLatestRun;
  const displayResourceCards = displayResourceCardsByRow.get(index) || [];
  // Rows folded under a "Worked for …" rollup are trace, not the final answer.
  // Trace is about what a row *is*, not about whether its run happens to be
  // folded. The most recent run never folds, so keying this off `groupedIndexes`
  // alone left its narration rendering at answer weight — which is why a turn
  // that talked before acting read as two answers.
  const withinTrace = completedRunTraceGroups.traceIndexes.has(index);
  // Spacing asks a different question from styling. `withinTrace` is about what
  // a row *is*; this is about whether it already sits inside a rollup that owns
  // its spacing. Guarding the compaction on `withinTrace` — after that flag was
  // widened to cover unfolded runs — turned it off for the whole live trace, so
  // every step stood a full turn-gap from the one before it.
  const isInsideRollup = completedRunTraceGroups.groupedIndexes.has(index);

  return (
    <div key={row.id || index} className={cn((compactAfterAssistant || compactActiveRunTrace) && !isInsideRollup && "-mt-3")}>
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
  onScroll: () => void;
  contentWidthClassName?: string;
  activeConversationId: string | null;
  showEmptyState: boolean;
  emptyState: ReactNode;
  /** Stretch the empty state over the whole viewport so it can dock to the composer. */
  fillEmptyState?: boolean;
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
  onRetryFailedMessage?: () => void;
  /**
   * Ask this computer's Agent Host to re-probe its coding agents.
   *
   * Only ever passed for a failure that says an agent is installed but signed
   * out, because that is the only failure it fixes — and it is the failure
   * where "try again" cannot work on its own: the harness stays AUTH_REQUIRED,
   * and admission keeps refusing, until the host looks again.
   */
  onRecheckLocalAgents?: () => void;
  showScrollToBottom: boolean;
  onScrollToBottom: () => void;
  isConversationBusy: boolean;
}

export function AssistantExperienceConversation({
  messagesContainerRef,
  onScroll,
  contentWidthClassName,
  activeConversationId,
  showEmptyState,
  emptyState,
  fillEmptyState = false,
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
  onRetryFailedMessage,
  onRecheckLocalAgents,
  showScrollToBottom,
  onScrollToBottom,
  isConversationBusy,
}: AssistantExperienceConversationProps) {
  // With no messages there is nothing to scroll, so the viewport stops claiming
  // the leftover height and shrinks to its content. The column above can then
  // centre the empty state and the composer together as one group, instead of
  // stranding the empty state at one end of a tall blank page.
  const shrinkToContent = fillEmptyState && showEmptyState;

  // Far longer than the 120ms a skeleton waits, because the two are answering
  // different questions. A skeleton appears once a wait is long enough to be
  // perceived at all; this appears only once a wait is long enough that silence
  // would be *misread* — an empty transcript is a real settled state here, so
  // saying nothing is right until the reader might conclude there is nothing to
  // say. No minimum visible time: arriving messages are their own resolution,
  // and a line reading "loading" underneath a transcript that has already landed
  // would describe a wait that is over.
  const showTranscriptWait = useLoadingGate(isInitialMessageLoading, {
    delayMs: TRANSCRIPT_WAIT_DELAY_MS,
    minVisibleMs: 0,
  });

  return (
    <AssistantMessageViewport
      ref={messagesContainerRef}
      onScroll={onScroll}
      className={cn(shrinkToContent && "max-h-full flex-none")}
      innerClassName={contentWidthClassName}
    >
      <div
        className="flex w-full flex-col gap-5"
        aria-live="polite"
        aria-atomic="false"
      >
      {showEmptyState ? emptyState : null}

      {/* Nothing for the first 600ms, and then one quiet line — never
          message-shaped placeholders. We do not know how many
          turns are coming or how tall they are, so any shape drawn here is a
          guess the real transcript re-flows the moment it lands. Blank is the
          honest fill, and it costs nothing: an empty transcript above a composer
          is a state this surface has to be able to draw anyway.

          The line sits at the end of the column, where the newest message will
          appear, so the arriving transcript pushes it out from the same edge
          rather than replacing a block somewhere above. It exists only so a slow
          fetch does not read as an empty conversation. */}
      {showTranscriptWait ? (
        <div className="flex w-full justify-start py-1">
          <InlineLoader size="xs" label="Loading conversation" className="text-xs text-[var(--text-tertiary)]" />
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

      {/* No entrance animation on this container. It is keyed by conversation,
          so `slide-in-from-bottom` played over the *entire history* every time
          you switched conversations — months of messages sliding up together to
          announce a load. Arrival motion belongs to a message as it appears, not
          to the transcript as a whole. */}
      <div
        key={activeConversationId || "new-conversation"}
        className="flex w-full flex-col gap-5"
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
          <div className="flex min-w-0 items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="break-words text-sm font-semibold leading-5">{assistantErrorTitle}</p>
              {assistantErrorDetails && assistantErrorDetails !== assistantErrorTitle ? (
                <pre className="mt-1.5 max-h-40 overflow-auto whitespace-pre-wrap break-words font-mono text-xs leading-5 text-[var(--text-secondary)]">
                  {assistantErrorDetails}
                </pre>
              ) : null}
            </div>
            <div className="flex shrink-0 items-center gap-1.5">
              {onRecheckLocalAgents ? (
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  onClick={onRecheckLocalAgents}
                  className="h-8 shrink-0 gap-1.5 bg-transparent px-2.5"
                >
                  <RefreshCw className="size-3.5" aria-hidden="true" />
                  Re-check
                </Button>
              ) : null}
              {onRetryFailedMessage ? (
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  onClick={onRetryFailedMessage}
                  className="h-8 shrink-0 gap-1.5 bg-transparent px-2.5"
                >
                  <RotateCcw className="size-3.5" aria-hidden="true" />
                  Retry
                </Button>
              ) : null}
            </div>
          </div>
        </div>
      ) : null}

      {showScrollToBottom ? (
      <Button
        type="button"
        variant="secondary"
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
      </div>
    </AssistantMessageViewport>
  );
}
