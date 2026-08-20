"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";
import type {
  AgentRuntimeConfig,
  AvailableModelInfo,
  ConversationModel,
  PlanStatus,
  PlanStepState,
  PlanSummaryState,
} from "lemma-sdk";
// Message-display pipeline (deduping, clustering, trace grouping, plan summary)
// now lives in the framework-agnostic core; the product consumes it from lemma-sdk.
import {
  buildDisplayMessageRows,
  findPendingUserApprovalInvocation,
  latestPlanSummary,
  latestUserIndex,
} from "lemma-sdk";
// Rows → turns: the conversation-shaped model (ask, work pill, speech,
// artifacts, interaction cards) the transcript renders.
import { buildChatTurns } from "@/lib/assistant/turns";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import type {
  AssistantRenderableMessage,
} from "lemma-sdk/react";
import type {
  AssistantControllerView,
  AssistantExperienceCustomizationProps,
  AssistantResourceMention,
  LemmaAssistantAppearance,
  LemmaAssistantDensity,
  LemmaAssistantRadius,
} from "./assistant-types";
import {
  type AssistantSurfaceTone,
} from "./assistant-chrome";
import {
  type DisplayResourceRequest,
} from "@/lib/assistant/display-resource";
// Pure formatting / label / tool-payload helpers (extracted from this file).
import {
  currentRunStatusModel,
  currentToolStatusLabel,
  stringifyAssistantError,
} from "./assistant-format";
// Message rendering cluster (tool rollups, run traces, approvals, resource cards,
// per-message group) extracted; AssistantExperienceView consumes these pieces.
import {
  currentPodIdFromBrowserPath,
  pluralize,
} from "./assistant-message-group";
// Standalone presentational parts (plan strip, thinking, empty state, icons) extracted.
import {
  EmptyState,
  LemmaMarkIcon,
  LiveRunStatusLine,
  ThinkingIndicator,
} from "./assistant-parts";
// Pure presentational helpers (class names, runtime labels, default renderers,
// suggestion-card parsing, @-mention matcher) extracted from this file.
import {
  assistantChromeStyleFromAppearance,
  assistantRootClassName,
  composerRuntimeLabel,
  defaultConversationLabel,
  defaultMessageContent,
  defaultPendingFile,
  getActiveResourceMention,
  isInlineAssistantErrorNoise,
} from "./assistant-experience-helpers";
// Self-contained hooks extracted from this file.
import { useControllableDraft } from "./use-assistant-experience";
import { useTranscriptScroll } from "./use-transcript-scroll";
// Presentational subtree views extracted from AssistantExperienceView's render.
import { AssistantExperienceSidebar } from "./assistant-experience-sidebar";
import { AssistantExperienceHeader } from "./assistant-experience-header";
import {
  AssistantExperienceConversation,
} from "./assistant-experience-conversation";
import { AssistantExperienceComposer } from "./assistant-experience-composer";
import { agentHostBridge, useIsDesktopShell } from "@/lib/desktop/agent-host-bridge";
import { isLocalAgentSignInFailure } from "@/components/agents/agent-runtime-helpers";
// getActiveToolBanner moved to assistant-format; re-export to preserve the API.
export { getActiveToolBanner } from "./assistant-format";

export type ToolCardArgs = Record<string, unknown>;
export type ToolCardResult = Record<string, unknown> & {
  success?: boolean;
  resourceType?: string;
  resourceId?: string;
  error?: string;
};

export type UserApprovalDecision = "APPROVE_ONCE" | "APPROVE_FOR_SESSION" | "DENY";
export type { PlanStatus, PlanStepState, PlanSummaryState };

export interface DisplayMessageRow {
  id: string;
  message: AssistantRenderableMessage;
  sourceIndexes: number[];
}

export interface ActiveToolBanner {
  summary: string;
  activeCount: number;
}

const SPARSE_HISTORY_ROW_TARGET = 8;
const SPARSE_HISTORY_AUTO_LOAD_LIMIT = 3;

export interface CompletedDisplayResourceCard {
  toolCallId: string;
  request: DisplayResourceRequest;
  href: string | null;
}

export type AssistantStatusPlacement = "inline" | "composer" | "none";

export interface AssistantExperienceViewProps extends AssistantExperienceCustomizationProps {
  controller: AssistantControllerView;
  appearance?: LemmaAssistantAppearance;
  density?: LemmaAssistantDensity;
  chromeStyle?: "elevated" | "subtle" | "flat";
  statusPlacement?: AssistantStatusPlacement;
  radius?: LemmaAssistantRadius;
  showHeader?: boolean;
  showModelPicker?: boolean;
  showNewConversationButton?: boolean;
  onNavigateResource?: (resourceType: string, resourceId: string, meta?: Record<string, unknown>) => void;
}

export function AssistantExperienceView({
  controller,
  title = "Lemma Assistant",
  subtitle = "Ask across your workspace and organization.",
  badge,
  headerLeadingActions,
  headerActions,
  composerModelControl,
  className,
  contentWidthClassName,
  composerWidthClassName,
  placeholder = "Message Lemma Assistant",
  emptyState,
  emptyStateSuggestions,
  emptyStateFillsViewport = false,
  resourceMentions = [],
  draft: controlledDraft,
  onDraftChange,
  showConversationList = false,
  appearance = "default",
  density = "comfortable",
  chromeStyle,
  statusPlacement = "inline",
  radius = "lg",
  showHeader = true,
  showModelPicker = false,
  showNewConversationButton = true,
  onNavigateResource,
  renderConversationLabel = defaultConversationLabel,
  renderMessageContent = defaultMessageContent,
  renderPendingFile = defaultPendingFile,
  renderToolInvocation,
}: AssistantExperienceViewProps) {
  const [draft, setDraft] = useControllableDraft(controlledDraft, onDraftChange);
  const [isPlanHidden, setIsPlanHidden] = useState(false);
  const [isUpdatingModel, setIsUpdatingModel] = useState(false);
  const [draftSelectionStart, setDraftSelectionStart] = useState(0);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const draftRestoredRef = useRef(false);
  const autoLoadedOlderConversationRef = useRef<string | null>(null);
  const autoLoadedOlderPageCountRef = useRef(0);
  const transcriptScroll = useTranscriptScroll({
    activeConversationId: controller.activeConversationId,
    onReachTop: () => loadOlderIfPossibleRef.current?.(),
  });
  // Destructured rather than read off the hook's return object: the object is
  // rebuilt every render, the callbacks inside it are not, and the memoized
  // transcript below can only see that if these keep their identities.
  const {
    containerRef: transcriptContainerRef,
    onScroll: transcriptOnScroll,
    isFollowing: transcriptIsFollowing,
    scrollToBottom,
    preserveAcross: preserveTranscriptScroll,
  } = transcriptScroll;
  const loadOlderIfPossibleRef = useRef<(() => void) | null>(null);
  const isRunActive = controller.isActiveConversationRunning;
  const isConversationBusy = controller.isLoading || isRunActive;
  const resolvedChromeStyle = chromeStyle ?? assistantChromeStyleFromAppearance(appearance);
  const controllerMessages = controller.messages;
  const activeConversationId = controller.activeConversationId;

  // Restore draft from localStorage when conversation changes
  useEffect(() => {
    draftRestoredRef.current = true;
    const key = `lemma:draft:${activeConversationId ?? 'new'}`;
    const stored = localStorage.getItem(key);
    setDraft(stored ?? '');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeConversationId]);

  // Persist draft to localStorage on change (skip the write immediately after a restore)
  useEffect(() => {
    if (draftRestoredRef.current) {
      draftRestoredRef.current = false;
      return;
    }
    const key = `lemma:draft:${activeConversationId ?? 'new'}`;
    if (draft) {
      localStorage.setItem(key, draft);
    } else {
      localStorage.removeItem(key);
    }
  }, [draft, activeConversationId]);
  const hasOlderMessages = controller.hasOlderMessages;
  const isLoadingMessages = controller.isLoadingMessages;
  const isLoadingOlderMessages = controller.isLoadingOlderMessages;
  const isInitialMessageLoading = isLoadingMessages && controllerMessages.length === 0;
  const isConversationEmpty = controllerMessages.length === 0 && !isConversationBusy && !isInitialMessageLoading;
  const centerEmptyConversation = emptyStateFillsViewport && isConversationEmpty;
  const sendMessage = controller.sendMessage;
  const uploadFiles = controller.uploadFiles;
  const loadOlderMessages = controller.loadOlderMessages;
  const setConversationModel = controller.setConversationModel;
  const pendingFileUploads = useMemo(
    () => controller.pendingFileUploads ?? controller.pendingFiles.map((file) => ({
      key: `${file.name}:${file.size}:${file.lastModified}`,
      file,
      status: "queued" as const,
      path: undefined,
      error: undefined,
    })),
    [controller.pendingFileUploads, controller.pendingFiles],
  );
  const hasPendingFileUploads = pendingFileUploads.length > 0;
  const uploadingFileCount = pendingFileUploads.filter((upload) => upload.status === "uploading").length;
  const failedFileCount = pendingFileUploads.filter((upload) => upload.status === "failed").length;
  const activeResourceMention = useMemo(
    () => {
      const cursorMention = getActiveResourceMention(draft, draftSelectionStart, resourceMentions);
      const endMention = getActiveResourceMention(draft, draft.length, resourceMentions);
      return endMention?.end === draft.length ? endMention : cursorMention;
    },
    [draft, draftSelectionStart, resourceMentions],
  );

  const availableModelOptions = useMemo<AvailableModelInfo[]>(
    () => {
      return controller.availableModels.filter((model) => model.id.trim().length > 0);
    },
    [controller.availableModels],
  );

  const resizeComposer = useCallback(() => {
    const textarea = inputRef.current;
    if (!textarea) return;

    const minHeight = density === "compact" ? 32 : 32;
    const maxHeight = density === "compact" ? 112 : 220;

    textarea.style.height = "auto";
    const nextHeight = draft.trim().length === 0
      ? minHeight
      : Math.min(maxHeight, Math.max(minHeight, textarea.scrollHeight));
    textarea.style.height = `${nextHeight}px`;
    textarea.style.overflowY = textarea.scrollHeight > maxHeight ? "auto" : "hidden";
  }, [density, draft]);

  useEffect(() => {
    resizeComposer();
  }, [draft, resizeComposer]);

  const displayMessageRows = useMemo(() => buildDisplayMessageRows(controllerMessages), [controllerMessages]);

  const displayResourcePodId = currentPodIdFromBrowserPath();
  const chatTurns = useMemo(
    () => buildChatTurns({
      rows: displayMessageRows,
      messages: controllerMessages,
      isRunActive,
      podId: displayResourcePodId,
      conversationId: activeConversationId,
    }),
    [displayMessageRows, controllerMessages, isRunActive, displayResourcePodId, activeConversationId],
  );
  const currentRunLatestUserIndex = latestUserIndex(controllerMessages);
  const activePendingApprovalInvocation = findPendingUserApprovalInvocation(displayMessageRows, currentRunLatestUserIndex);
  // A pending question or approval lives in the transcript as a card now, so
  // the composer stays put — but answering is the card's job, so the composer
  // refuses to send until the interaction resolves.
  const interactionPending = !!activePendingApprovalInvocation;

  const canLoadOlder = hasOlderMessages && !isLoadingMessages && !isLoadingOlderMessages;
  const loadOlder = useCallback(() => {
    if (!canLoadOlder) return;
    void preserveTranscriptScroll(loadOlderMessages);
  }, [canLoadOlder, loadOlderMessages, preserveTranscriptScroll]);
  useEffect(() => {
    loadOlderIfPossibleRef.current = loadOlder;
  }, [loadOlder]);

  // Switching conversations puts the caret back in the composer, so a reader who
  // changed threads can just start typing. The transcript's own jump to the
  // newest message is the scroll hook's business.
  useEffect(() => {
    const frame = requestAnimationFrame(() => inputRef.current?.focus());
    return () => cancelAnimationFrame(frame);
  }, [activeConversationId]);

  useEffect(() => {
    if (autoLoadedOlderConversationRef.current !== activeConversationId) {
      autoLoadedOlderConversationRef.current = activeConversationId;
      autoLoadedOlderPageCountRef.current = 0;
    }

    if (!activeConversationId) return;
    if (!canLoadOlder) return;
    if (displayMessageRows.length === 0 || displayMessageRows.length >= SPARSE_HISTORY_ROW_TARGET) return;
    if (autoLoadedOlderPageCountRef.current >= SPARSE_HISTORY_AUTO_LOAD_LIMIT) return;

    autoLoadedOlderPageCountRef.current += 1;
    loadOlder();
  }, [activeConversationId, canLoadOlder, displayMessageRows.length, loadOlder]);

  const detectedPlanSummary = useMemo(() => latestPlanSummary(controllerMessages), [controllerMessages]);
  const planSummary = detectedPlanSummary?.isComplete ? null : detectedPlanSummary;
  const latestUserMessageId = useMemo(
    () => [...controllerMessages].reverse().find((message) => message.role === "user")?.id ?? null,
    [controllerMessages],
  );
  const planIdentity = planSummary?.steps.map((step) => step.step).join("\u0000") ?? null;
  useEffect(() => {
    setIsPlanHidden(false);
  }, [activeConversationId, latestUserMessageId, planIdentity]);
  // The run's live status, clockless: the elapsed second is owned by the two
  // leaves that display it — the live turn's pill and the composer's line — so
  // a ticking clock never re-renders the transcript. (It used to: a setInterval
  // here, a `nowMs` prop fanned out to every turn, once a second, all run.)
  const runStatusModel = useMemo(
    () => currentRunStatusModel({
      messages: controllerMessages,
      rows: displayMessageRows,
      isConversationBusy: isRunActive,
    }),
    [controllerMessages, displayMessageRows, isRunActive],
  );
  const inlineToolStatus = useMemo(
    () => currentToolStatusLabel({
      messages: controllerMessages,
      isConversationBusy: isRunActive,
      streamingTool: controller.streamingTool,
    }),
    [controller.streamingTool, controllerMessages, isRunActive],
  );
  // The live turn's status pill wears these. When the composer owns status
  // display instead, the pill falls back to a bare "Working".
  const liveToolLabel = statusPlacement === "inline"
    ? inlineToolStatus?.label ?? null
    : null;
  const liveRunStatus = statusPlacement === "inline" ? runStatusModel : null;

  const handleSubmit = useCallback(async () => {
    if ((!draft.trim() && !hasPendingFileUploads) || isConversationBusy || interactionPending) return;
    const message = draft.trim();
    setDraft("");
    scrollToBottom("smooth");
    await sendMessage(message);
  }, [draft, hasPendingFileUploads, isConversationBusy, interactionPending, scrollToBottom, sendMessage, setDraft]);

  const handleSuggestionSend = useCallback(async (suggestion: string) => {
    const message = suggestion.trim();
    if (!message || isConversationBusy) return;
    scrollToBottom("smooth");
    await sendMessage(message);
  }, [isConversationBusy, scrollToBottom, sendMessage]);

  // Stable identities for the memoized transcript: an inline lambda here would
  // be a new prop every render and defeat the memo.
  const handleScrollToBottom = useCallback(() => {
    scrollToBottom("smooth");
  }, [scrollToBottom]);
  const retryFailedMessage = controller.retryFailedMessage;
  const canRetryFailedMessage = controller.canRetryFailedMessage;
  const handleRetryFailedMessage = useCallback(() => {
    if (canRetryFailedMessage && retryFailedMessage) void retryFailedMessage();
  }, [canRetryFailedMessage, retryFailedMessage]);

  const handleUploadSelection = useCallback(async (files: FileList | null) => {
    const selectedFiles = files ? Array.from(files) : [];
    if (selectedFiles.length === 0) return;

    try {
      await uploadFiles(selectedFiles, { deferUntilSend: true });
      requestAnimationFrame(() => {
        inputRef.current?.focus();
      });
    } finally {
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
    }
  }, [uploadFiles]);

  const updateDraftSelection = useCallback(() => {
    const textarea = inputRef.current;
    setDraftSelectionStart(textarea?.selectionStart ?? draft.length);
  }, [draft.length]);

  const insertResourceMention = useCallback((mention: AssistantResourceMention) => {
    if (!activeResourceMention) return;

    const nextDraft = [
      draft.slice(0, activeResourceMention.start),
      mention.insertText,
      " ",
      draft.slice(activeResourceMention.end),
    ].join("");
    const nextCursor = activeResourceMention.start + mention.insertText.length + 1;

    setDraft(nextDraft);
    setDraftSelectionStart(nextCursor);
    requestAnimationFrame(() => {
      inputRef.current?.focus();
      inputRef.current?.setSelectionRange(nextCursor, nextCursor);
    });
  }, [activeResourceMention, draft, setDraft]);

  const handleKeyDown = useCallback((event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (activeResourceMention && activeResourceMention.items.length > 0) {
      if (event.key === "Tab") {
        event.preventDefault();
        insertResourceMention(activeResourceMention.items[0]);
        return;
      }
      if (event.key === "Escape") {
        setDraftSelectionStart(-1);
        return;
      }
    }

    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void handleSubmit();
    }
  }, [activeResourceMention, handleSubmit, insertResourceMention]);

  const handleModelChange = useCallback(async (nextModel: string | null, runtime?: AgentRuntimeConfig | null) => {
    if (isUpdatingModel) return;
    setIsUpdatingModel(true);
    try {
      await setConversationModel(nextModel as ConversationModel | null, runtime ?? null);
    } finally {
      setIsUpdatingModel(false);
    }
  }, [isUpdatingModel, setConversationModel]);

  const runtimeLabel = composerRuntimeLabel(
    controller.conversationModel,
    controller.conversationRuntime ?? null,
    availableModelOptions,
  );

  const isDesktopShell = useIsDesktopShell();
  // Ask the host to look again, then let the user send the message again. The
  // status this returns is one poll behind by design, so nothing here waits on
  // it -- the harness list and the composer both re-read it on their own.
  const recheckLocalAgents = useCallback(() => {
    void agentHostBridge.refresh().then(
      () => toast.success("Rechecking the coding agents on this Mac"),
      (error: unknown) => toast.error(error instanceof Error ? error.message : String(error)),
    );
  }, []);

  const assistantErrorDetails = stringifyAssistantError(controller.error).trim();
  const showAssistantErrorInTranscript = !!controller.error && !isInlineAssistantErrorNoise(assistantErrorDetails);
  // Offered only for the failure it fixes, and only where there is a host to
  // ask. Outside the desktop shell `agentHostBridge` has nothing to call, and a
  // button that throws is worse than no button.
  const canRecheckLocalAgents = isDesktopShell && isLocalAgentSignInFailure(assistantErrorDetails);
  const assistantErrorTitle = assistantErrorDetails && assistantErrorDetails.length <= 120 && !assistantErrorDetails.includes("\n")
    ? assistantErrorDetails
    : "Assistant error";
  const headerTone: AssistantSurfaceTone = resolvedChromeStyle === "elevated" ? "default" : resolvedChromeStyle === "flat" ? "flat" : "subtle";
  const composerTone: AssistantSurfaceTone = resolvedChromeStyle === "flat" ? "flat" : resolvedChromeStyle === "subtle" ? "subtle" : "default";
  // The transcript's live status is the running turn's pill, not a bottom line.
  // The composer only carries it when a mount asks for that placement.
  const showThinkingStatus = !!runStatusModel;
  const showComposerStatus = statusPlacement === "composer" && showThinkingStatus;
  const uploadStatusLabel = controller.isUploadingFiles
    ? uploadingFileCount > 0
      ? `Uploading ${pluralize(uploadingFileCount, "file")}`
      : "Preparing files"
    : failedFileCount > 0
      ? `${pluralize(failedFileCount, "file")} failed to upload`
      : null;
  const hasComposerStatus = showComposerStatus || !!uploadStatusLabel;
  const composerStatus = (
    <>
      {showComposerStatus && runStatusModel ? (
        <LiveRunStatusLine status={runStatusModel} />
      ) : null}
      {uploadStatusLabel ? (
        <ThinkingIndicator label={uploadStatusLabel} shimmer={controller.isUploadingFiles} />
      ) : null}
    </>
  );
  const resolvedHeaderBadge = badge === undefined
    ? <LemmaMarkIcon className="size-4.5 text-[var(--text-on-brand)]" />
    : badge;
  // Memoized element: the transcript is memoized on its props, and an element
  // rebuilt every render is a changed prop.
  const emptyStateElement = useMemo(
    () => emptyState || (
      <EmptyState
        onSendMessage={(message) => { void handleSuggestionSend(message); }}
        suggestions={emptyStateSuggestions}
        density={density}
      />
    ),
    [emptyState, emptyStateSuggestions, density, handleSuggestionSend],
  );

  return (
    <div
      className={cn(assistantRootClassName(appearance, radius, showConversationList), className)}
      data-appearance={appearance}
      data-density={density}
      data-chrome-style={resolvedChromeStyle}
      data-status-placement={statusPlacement}
      data-radius={radius}
      data-show-model-picker={showModelPicker ? "true" : "false"}
      data-busy={isConversationBusy ? "true" : "false"}
      data-has-plan={planSummary ? "true" : "false"}
      data-has-pending-files={controller.pendingFiles.length > 0 ? "true" : "false"}
      data-show-conversation-list={showConversationList ? "true" : "false"}
    >
      {showConversationList ? (
        <AssistantExperienceSidebar
          controller={controller}
          appearance={appearance}
          radius={radius}
          showNewConversationButton={showNewConversationButton}
          renderConversationLabel={renderConversationLabel}
        />
      ) : null}

      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
        <div
          className={cn(
            "flex min-h-0 flex-1 flex-col overflow-hidden bg-[var(--pod-main-bg)]",
            // An empty conversation is a cold start, not a transcript: centre the
            // empty state and the composer as one group rather than pinning the
            // composer to the floor of a blank page.
            centerEmptyConversation && "justify-center",
          )}
        >
          {showHeader ? (
            <AssistantExperienceHeader
              controller={controller}
              headerTone={headerTone}
              title={title}
              subtitle={subtitle}
              badge={resolvedHeaderBadge}
              headerLeadingActions={headerLeadingActions}
              headerActions={headerActions}
              density={density}
              showModelPicker={showModelPicker}
              showNewConversationButton={showNewConversationButton}
              availableModelOptions={availableModelOptions}
              isConversationBusy={isConversationBusy}
              isUpdatingModel={isUpdatingModel}
              onModelChange={(nextModel, runtime) => { void handleModelChange(nextModel, runtime); }}
            />
          ) : null}

          <AssistantExperienceConversation
            messagesContainerRef={transcriptContainerRef}
            onScroll={transcriptOnScroll}
            contentWidthClassName={contentWidthClassName}
            activeConversationId={activeConversationId}
            showEmptyState={isConversationEmpty}
            fillEmptyState={emptyStateFillsViewport}
            emptyState={emptyStateElement}
            isInitialMessageLoading={isInitialMessageLoading}
            hasOlderMessages={hasOlderMessages}
            isLoadingMessages={isLoadingMessages}
            isLoadingOlderMessages={isLoadingOlderMessages}
            hasMessages={controller.messages.length > 0}
            onLoadOlder={loadOlder}
            turns={chatTurns}
            podId={displayResourcePodId}
            onResolveUserApproval={controller.resolveUserApproval}
            liveToolLabel={liveToolLabel}
            liveRunStatus={liveRunStatus}
            onNavigateResource={onNavigateResource}
            renderMessageContent={renderMessageContent}
            renderToolInvocation={renderToolInvocation}
            showAssistantErrorInTranscript={showAssistantErrorInTranscript}
            assistantErrorTitle={assistantErrorTitle}
            assistantErrorDetails={assistantErrorDetails}
            onRetryFailedMessage={canRetryFailedMessage && retryFailedMessage
              ? handleRetryFailedMessage
              : undefined}
            onRecheckLocalAgents={canRecheckLocalAgents ? recheckLocalAgents : undefined}
            showScrollToBottom={!transcriptIsFollowing}
            onScrollToBottom={handleScrollToBottom}
            isConversationBusy={isConversationBusy}
          />
        </div>

        <AssistantExperienceComposer
          composerTone={composerTone}
          composerWidthClassName={composerWidthClassName}
          planSummary={planSummary}
          isPlanHidden={isPlanHidden}
          onShowPlan={() => setIsPlanHidden(false)}
          onHidePlan={() => setIsPlanHidden(true)}
          hasComposerStatus={hasComposerStatus}
          composerStatus={composerStatus}
          pendingFileUploads={pendingFileUploads}
          renderPendingFile={renderPendingFile}
          controller={controller}
          interactionPending={interactionPending}
          activeResourceMention={activeResourceMention}
          insertResourceMention={insertResourceMention}
          radius={radius}
          density={density}
          fileInputRef={fileInputRef}
          inputRef={inputRef}
          draft={draft}
          placeholder={interactionPending ? "Respond above to continue" : placeholder}
          isConversationBusy={isConversationBusy}
          hasPendingFileUploads={hasPendingFileUploads}
          runtimeLabel={runtimeLabel}
          composerModelControl={composerModelControl}
          onUploadSelection={(files) => { void handleUploadSelection(files); }}
          onDraftChange={(event) => {
            setDraft(event.target.value);
            setDraftSelectionStart(event.currentTarget.selectionStart ?? event.target.value.length);
          }}
          onKeyDown={handleKeyDown}
          onUpdateDraftSelection={updateDraftSelection}
          onSubmit={() => { void handleSubmit(); }}
        />
      </div>
    </div>
  );
}
