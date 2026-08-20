"use client";

import { Fragment, memo, type ReactNode, type RefObject } from "react";
import { cn } from "@/lib/utils";
import { useLoadingGate } from "@/components/shared/loading";
import { InlineLoader } from "@/components/brand/loader";
import { Button } from "@/components/ui/button";
import { ArrowDown, RefreshCw, RotateCcw } from "@/components/ui/icons";
import { dayMarkLabel, sameDay, turnDayDate, type ChatTurn } from "@/lib/assistant/turns";
import type {
  AssistantMessageRenderArgs,
  AssistantToolRenderArgs,
} from "./assistant-types";
import type { LiveRunStatus } from "./assistant-format";
import type { UserApprovalDecision } from "./assistant-experience";
import { AssistantMessageViewport } from "./assistant-chrome";
import { AssistantTurnView } from "./assistant-turn";

/** How long a transcript stays silently empty before it admits it is fetching. */
const TRANSCRIPT_WAIT_DELAY_MS = 600;

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
  /** The transcript as turns — ask, work pill, speech, artifacts — not rows. */
  turns: ChatTurn[];
  podId: string | null;
  onResolveUserApproval?: (approvalId: string, decision: UserApprovalDecision, response?: Record<string, unknown> | null) => Promise<void>;
  /** Live-only inputs for the running turn's pill; the pill owns the tick. */
  liveToolLabel: string | null;
  liveRunStatus: LiveRunStatus | null;
  onNavigateResource?: (resourceType: string, resourceId: string, meta?: Record<string, unknown>) => void;
  renderMessageContent: (args: AssistantMessageRenderArgs) => ReactNode;
  renderToolInvocation?: (args: AssistantToolRenderArgs) => ReactNode;
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

// Memoized so the transcript only re-renders when the transcript changes —
// never on a keystroke in the composer, never on a model picker toggle. The
// turns array changes identity on every streaming flush (it must), and the
// turn views inside are memoized again on their fingerprints, so a flush
// re-renders this shell and the one live turn, not the history.
export const AssistantExperienceConversation = memo(function AssistantExperienceConversation({
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
  turns,
  podId,
  onResolveUserApproval,
  liveToolLabel,
  liveRunStatus,
  onNavigateResource,
  renderMessageContent,
  renderToolInvocation,
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
      {showEmptyState ? <div className="lchat-empty-in w-full">{emptyState}</div> : null}

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
        className="lchat-col"
      >
      {turns.map((turn, index) => {
        const day = turnDayDate(turn);
        const previousDay = index > 0 ? turnDayDate(turns[index - 1]) : null;
        const showDayMark = !!day && (!previousDay || !sameDay(day, previousDay));
        return (
          <Fragment key={turn.id}>
            {showDayMark ? <div className="lchat-daymark">{dayMarkLabel(day)}</div> : null}
            <AssistantTurnView
              turn={turn}
              activeConversationId={activeConversationId}
              podId={podId}
              liveToolLabel={turn.isLive ? liveToolLabel : null}
              liveRunStatus={turn.isLive ? liveRunStatus : null}
              onNavigateResource={onNavigateResource}
              onResolveUserApproval={onResolveUserApproval}
              renderMessageContent={renderMessageContent}
              renderToolInvocation={renderToolInvocation}
            />
          </Fragment>
        );
      })}
      </div>

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
});
