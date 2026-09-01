"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
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
  isAskUserToolName,
  latestPlanSummary,
  latestUserIndex,
} from "lemma-sdk";
// Rows → turns: the conversation-shaped model (ask, work pill, speech,
// artifacts, interaction cards) the transcript renders.
import { addressedAgentName } from "@/lib/assistant/addressed-agent";
import { buildChatTurns, interactionAnchorId } from "@/lib/assistant/turns";
import { toast } from "sonner";
import { thisComputer } from "@/lib/desktop/this-computer";
import { cn } from "@/lib/utils";
import { DEFAULT_RESPONDER_NAME } from "@/lib/utils/agents";
import { LEM_SEED } from "@/lib/identity/seeded-identity";
import { ResourceIdentity } from "@/components/shared/resource-identity";
import { Button } from "@/components/ui/button";
import type {
  AssistantRenderableMessage,
} from "lemma-sdk/react";
import type {
  AssistantControllerView,
  AssistantExperienceCustomizationProps,
  AssistantParticipant,
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

/** How long typing has to pause before the draft is written to localStorage. */
const DRAFT_PERSIST_DEBOUNCE_MS = 400;

/**
 * Does the browser grow the composer on its own?
 *
 * `composer.css` asks for `field-sizing: content` with a `min-height` and a
 * `max-height`, which is the whole of what the JS fallback below computes.
 * Where it is honoured — Chrome and Edge 123+, Safari 26+ — the fallback has to
 * stay out of the way, because measuring costs a forced layout per keystroke
 * and buys nothing. Firefox has no support yet, so the fallback is not dead
 * code; it is the only thing sizing the box there.
 *
 * Answered once and cached: the support does not change under a running tab,
 * and this is asked on the keystroke path. Lazily, not at module scope, so the
 * server's evaluation of this module never decides it for the browser.
 */
let fieldSizingSupport: boolean | null = null;
function cssSizesTheComposer(): boolean {
  if (fieldSizingSupport === null) {
    fieldSizingSupport = typeof CSS !== "undefined"
      && typeof CSS.supports === "function"
      && CSS.supports("field-sizing", "content");
  }
  return fieldSizingSupport;
}

function writeDraft(key: string, draft: string) {
  if (draft) {
    localStorage.setItem(key, draft);
  } else {
    localStorage.removeItem(key);
  }
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
  /**
   * Who is reading. Passed in rather than fetched: this view is used by the pod
   * assistant, the agent test panel and the flow run inspector, and only the
   * first of those is a conversation somebody else can be in.
   *
   * Omitting it withholds nothing, which is the correct default for a
   * transcript nobody else can see.
   */
  viewerId?: string | null;
  /**
   * Everyone in the conversation. Only used to put a name on somebody else's
   * turn, so an empty list simply means no bylines — which is right for the
   * conversations that have only ever had one person in them.
   */
  participants?: AssistantParticipant[];
  /** Rendered in the composer's control row. See the composer prop. */
  participantsControl?: ReactNode;
  onNavigateResource?: (resourceType: string, resourceId: string, meta?: Record<string, unknown>) => void;
}

export function AssistantExperienceView({
  controller,
  title = DEFAULT_RESPONDER_NAME,
  subtitle = "Ask across your workspace and organization.",
  badge,
  headerLeadingActions,
  headerActions,
  composerModelControl,
  className,
  contentWidthClassName,
  composerWidthClassName,
  placeholder = `Message ${DEFAULT_RESPONDER_NAME}`,
  emptyState,
  emptyStateSuggestions,
  emptyStateFillsViewport = false,
  resourceMentions = [],
  draft: controlledDraft,
  onDraftChange,
  showConversationList = false,
  viewerId = null,
  participants,
  participantsControl,
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
  const pendingDraftWriteRef = useRef<{ key: string; draft: string } | null>(null);
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

  // Persist draft to localStorage on change (skip the write immediately after a
  // restore). Deferred rather than written inline: `localStorage` is
  // synchronous, and this used to run once per keystroke — a main-thread write
  // between the keypress and the frame that draws it. The draft only has to
  // survive a reload, so a pause in typing is soon enough, and the cleanup
  // flushes it if the composer goes away first.
  useEffect(() => {
    const key = `lemma:draft:${activeConversationId ?? 'new'}`;
    // Recorded on every commit, debounced or not, so the unmount flush below
    // always has the latest draft and the key it belongs to.
    pendingDraftWriteRef.current = { key, draft };
    if (draftRestoredRef.current) {
      draftRestoredRef.current = false;
      return;
    }
    const timer = window.setTimeout(() => writeDraft(key, draft), DRAFT_PERSIST_DEBOUNCE_MS);
    return () => {
      window.clearTimeout(timer);
    };
  }, [draft, activeConversationId]);

  // A draft still sitting in the debounce when the composer unmounts would
  // otherwise be lost, so the last one is written out on the way down.
  useEffect(() => () => {
    const pending = pendingDraftWriteRef.current;
    if (pending) writeDraft(pending.key, pending.draft);
  }, []);
  const hasOlderMessages = controller.hasOlderMessages;
  const isLoadingMessages = controller.isLoadingMessages;
  const isLoadingOlderMessages = controller.isLoadingOlderMessages;
  const isInitialMessageLoading = isLoadingMessages && controllerMessages.length === 0;
  const isConversationEmpty = controllerMessages.length === 0 && !isConversationBusy && !isInitialMessageLoading;
  const centerEmptyConversation = emptyStateFillsViewport && isConversationEmpty;
  const sendMessage = controller.sendMessage;
  const steerMessage = controller.steerMessage;
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

    // Where the browser sizes the box itself, this measurement is not merely
    // redundant — it is the flicker. `field-sizing: content` in `composer.css`
    // grows the input from its own content between the same floor and ceiling
    // this computes, so all the work below buys is a write of `height:auto`, a
    // forced synchronous layout to read `scrollHeight`, and a write of the
    // height back: the composer collapsing and returning inside one keystroke.
    // A mobile browser with the keyboard up answers any layout change around
    // the caret by scrolling it back into view, and with the keyboard owning
    // the bottom of the screen that scroll moves the transcript. Once per
    // character typed, the whole conversation jumped.
    if (cssSizesTheComposer()) return;

    const minHeight = density === "compact" ? 32 : 32;
    const maxHeight = density === "compact" ? 112 : 220;

    // An empty composer is always one row, and reading `scrollHeight` after
    // resetting the height is a forced synchronous layout — so the common
    // keystroke, the one that leaves the box a single line, no longer pays for
    // one. Only text that could wrap measures.
    if (draft.trim().length === 0) {
      textarea.style.height = `${minHeight}px`;
      textarea.style.overflowY = "hidden";
      return;
    }

    textarea.style.height = "auto";
    const scrollHeight = textarea.scrollHeight;
    textarea.style.height = `${Math.min(maxHeight, Math.max(minHeight, scrollHeight))}px`;
    textarea.style.overflowY = scrollHeight > maxHeight ? "auto" : "hidden";
  }, [density, draft]);

  useEffect(() => {
    resizeComposer();
  }, [draft, resizeComposer]);

  const displayMessageRows = useMemo(() => buildDisplayMessageRows(controllerMessages), [controllerMessages]);
  // Built once per roster rather than scanned per turn: a long transcript
  // renders every turn and each one would otherwise walk the whole list.
  const senderNames = useMemo(() => {
    const names = new Map<string, string>();
    for (const participant of participants ?? []) {
      if (participant.user_id && participant.display_name) {
        names.set(participant.user_id, participant.display_name);
      }
    }
    return names;
  }, [participants]);
  const agentNamesById = useMemo(() => {
    const names = new Map<string, string>();
    for (const participant of participants ?? []) {
      if (participant.agent_id && participant.display_name) {
        names.set(participant.agent_id, participant.display_name);
      }
    }
    return names;
  }, [participants]);
  // Every turn is labelled once the room holds more than one voice -- two
  // people, or an agent that is not the only one who could have answered.
  // Below that there is nothing to disambiguate and a name on every bubble is
  // just noise, which is why a plain one-to-one chat is left alone.
  const named = senderNames.size > 1 || agentNamesById.size > 1;
  const resolveSenderName = useMemo(
    () => (named
      ? (userId: string) => senderNames.get(userId) ?? null
      : undefined),
    [named, senderNames],
  );
  const resolveAgentName = useMemo(
    () => (named
      ? (agentId: string) => agentNamesById.get(agentId) ?? null
      : undefined),
    [named, agentNamesById],
  );

  const displayResourcePodId = currentPodIdFromBrowserPath();
  const chatTurns = useMemo(
    () => buildChatTurns({
      rows: displayMessageRows,
      messages: controllerMessages,
      viewerId,
      // A send in flight counts as live, not just a run the server has already
      // confirmed. The turn goes on screen the moment you press enter, but the
      // conversation is not reported RUNNING for a few hundred milliseconds
      // after that — and the transcript's arrival motion is keyed off the
      // turn's liveness, so the gap meant your message painted solid and then
      // replayed its entrance once the status caught up. That flicker was the
      // whole of it: nothing remounted, a CSS rule simply started matching an
      // element that was already on screen.
      isRunActive: isConversationBusy,
      podId: displayResourcePodId,
      conversationId: activeConversationId,
    }),
    [displayMessageRows, controllerMessages, isConversationBusy, displayResourcePodId, activeConversationId, viewerId],
  );
  const currentRunLatestUserIndex = latestUserIndex(controllerMessages);
  const activePendingApprovalInvocation = findPendingUserApprovalInvocation(displayMessageRows, currentRunLatestUserIndex);
  // A pending question or approval lives in the transcript as a card now, so
  // the composer stays put — but answering is the card's job, so the composer
  // refuses to send until the interaction resolves.
  const interactionPending = !!activePendingApprovalInvocation;
  // A blocked composer has to be able to point at what is blocking it. The card
  // is the only way to answer and the input refuses to send until it resolves,
  // so if the reader has scrolled away from it — or a tall card below it pushed
  // it off screen — the conversation has no visible way forward.
  const pendingInteractionCallId = activePendingApprovalInvocation?.toolCallId ?? null;
  const pendingInteractionIsAsk = !!activePendingApprovalInvocation
    && isAskUserToolName(activePendingApprovalInvocation.toolName);
  const scrollToPendingInteraction = useCallback(() => {
    if (!pendingInteractionCallId) return;
    document
      .getElementById(interactionAnchorId(pendingInteractionCallId))
      ?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [pendingInteractionCallId]);

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

  // The agents in the room, by the name an `@mention` would use. Rebuilt only
  // when the roster changes, not per keystroke.
  const agentNames = useMemo(
    () => (participants ?? [])
      .filter((participant) => participant.agent_id && participant.display_name)
      .map((participant) => participant.display_name as string),
    [participants],
  );

  const handleSubmit = useCallback(async () => {
    if ((!draft.trim() && !hasPendingFileUploads) || interactionPending) return;
    const message = draft.trim();
    setDraft("");
    scrollToBottom("smooth");
    // A run already in flight takes the follow-up as a steer: it joins that run
    // rather than starting a second one. Otherwise identical to a send —
    // attachments included, because the two go to the same endpoint shape and a
    // dropped file with no explanation is worse than either outcome.
    if (isConversationBusy) {
      // A steer joins the run already going, so it never picks a new responder.
      await steerMessage(message);
      return;
    }
    // `@name` in the text is what routes the turn. Read here rather than on the
    // server: the composer already knows who is in the room, and a name the
    // server had to guess at could reach an agent nobody added.
    const addressed = addressedAgentName(message, agentNames);
    await sendMessage(message, addressed ? { agentName: addressed } : undefined);
  }, [draft, hasPendingFileUploads, isConversationBusy, interactionPending, scrollToBottom, sendMessage, steerMessage, setDraft, agentNames]);

  // Only the empty state offers suggestions, and it renders under
  // `showEmptyState={isConversationEmpty}` — which requires nothing to be
  // running. So there is no busy case to handle here; a branch for one was
  // unreachable code that read like a second, disagreeing rule.
  const handleSuggestionSend = useCallback(async (suggestion: string) => {
    const message = suggestion.trim();
    if (!message || interactionPending) return;
    scrollToBottom("smooth");
    await sendMessage(message);
  }, [interactionPending, scrollToBottom, sendMessage]);

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
      () => toast.success(`Rechecking the coding agents on ${thisComputer()}`),
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
    : `${DEFAULT_RESPONDER_NAME} hit an error`;
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
  const hasComposerStatus = showComposerStatus || !!uploadStatusLabel || interactionPending;
  const composerStatus = (
    <>
      {interactionPending ? (
        <Button
          type="button"
          variant="link"
          size="xs"
          onClick={scrollToPendingInteraction}
          className="h-auto px-0 text-xs font-normal"
        >
          {pendingInteractionIsAsk
            ? "Answer the question to continue"
            : "Approve or reject to continue"}
        </Button>
      ) : null}
      {showComposerStatus && runStatusModel ? (
        <LiveRunStatusLine status={runStatusModel} />
      ) : null}
      {uploadStatusLabel ? (
        <ThinkingIndicator label={uploadStatusLabel} shimmer={controller.isUploadingFiles} />
      ) : null}
    </>
  );
  // The dock's badge is Lem itself, drawn by the same renderer as the sidebar
  // row and the front door, so the thing answering here is visibly the thing
  // you clicked. It was a generic shield-and-check glyph on a brand tile, which
  // named a category rather than a responder.
  const resolvedHeaderBadge = badge === undefined
    ? (
      <ResourceIdentity
        seed={LEM_SEED}
        label={DEFAULT_RESPONDER_NAME}
        kind="being"
        size={density === "compact" ? 28 : 36}
      />
    )
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
            resolveSenderName={resolveSenderName}
            resolveAgentName={resolveAgentName}
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
          participantsControl={participantsControl}
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
