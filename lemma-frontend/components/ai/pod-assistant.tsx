"use client";

import { useRouter } from "next/navigation";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from "react";
import {
  Maximize2,
  MessageSquare,
  Minimize2,
  PanelLeftClose,
  PanelLeftOpen,
  X,
  XCircle,
} from "@/components/ui/icons";
import { AssistantExperienceView } from "@/components/lemma/assistant/assistant-experience";
import type {
  AssistantControllerView,
  AssistantResourceMention,
} from "@/components/lemma/assistant/assistant-types";
import { Button } from "@/components/ui/button";
import {
  DEFAULT_ASSISTANT_DOCK_WIDTH,
  closestAssistantDockWidth,
  type AssistantDockWidth,
  type AssistantPresentation,
} from "@/components/pod/pod-layout-context";
import { useDatastoreFiles, useTables } from "@/lib/hooks/use-datastores";
import { cn } from "@/lib/utils";
import { getConversationStatusView, type ConversationStatusView } from "@/lib/utils/conversations";
import { useAIAssistant } from "./ai-assistant-context";

const ASSISTANT_PREFILL_EVENT = "lemma-assistant-prefill-draft";
const DEFAULT_DATASTORE_NAME = "default";

function ConversationStatusPill({ statusView }: { statusView: ConversationStatusView }) {
  if (statusView.state === "unknown" || statusView.state === "completed") return null;

  return (
    <span
      className={cn(
        "inline-flex h-6 shrink-0 items-center gap-1.5 rounded-full border px-2 text-xs font-medium",
        statusView.tone === "live" && "state-badge-brand",
        statusView.tone === "warning" && "state-badge-warning",
        statusView.tone === "danger" && "state-badge-error",
        statusView.tone === "muted" && "chip-muted"
      )}
    >
      {statusView.state === "failed" ? (
        <XCircle className="h-3 w-3" strokeWidth={1.9} />
      ) : (
        <span
          className={cn(
            "h-1.5 w-1.5 rounded-full bg-current",
            statusView.isActive && "animate-pulse"
          )}
        />
      )}
      {statusView.label}
    </span>
  );
}

interface AssistantPrefillDetail {
  content: string;
  forceNewConversation?: boolean;
}

interface PodAssistantEmbeddedProps {
  title?: string;
  subtitle?: string;
  placeholder?: string;
  className?: string;
  density?: "compact" | "comfortable" | "spacious";
  showHeader?: boolean;
  showModelPicker?: boolean;
  composerModelControl?: ReactNode;
  showNewConversationButton?: boolean;
  showConversationList?: boolean;
  contentWidthClassName?: string;
  composerWidthClassName?: string;
  emptyState?: ReactNode;
  draft?: string;
  onDraftChange?: (value: string) => void;
}

function parseAssistantPrefillDetail(
  value: unknown,
): AssistantPrefillDetail | null {
  if (!value || typeof value !== "object") return null;
  const detail = value as Partial<AssistantPrefillDetail>;
  if (
    typeof detail.content !== "string" ||
    detail.content.trim().length === 0
  ) {
    return null;
  }

  return {
    content: detail.content.trim(),
    forceNewConversation: detail.forceNewConversation === true,
  };
}

function getFileMentionPath(value: unknown): string {
  if (!value || typeof value !== "object" || Array.isArray(value)) return "";
  const record = value as Record<string, unknown>;
  const path =
    record.path ??
    record.file_path ??
    record.filePath ??
    record.name ??
    record.filename;
  return typeof path === "string" ? path.trim() : "";
}

function fileMentionLabel(path: string): string {
  const parts = path.replace(/\\/g, "/").split("/").filter(Boolean);
  return parts[parts.length - 1] || path;
}

export function AssistantToolIcon({
  className = "text-[var(--text-secondary)]",
}: { className?: string } = {}) {
  return (
    <MessageSquare aria-hidden="true" className={cn("h-4 w-4", className)} strokeWidth={1.9} />
  );
}

function buildControllerView(
  assistant: ReturnType<typeof useAIAssistant>,
): AssistantControllerView {
  return {
    messages: assistant.messages,
    conversations: assistant.conversations,
    activeConversationId: assistant.openedConversationId,
    availableModels: assistant.availableModels,
    conversationModel: assistant.conversationModel,
    conversationRuntime: assistant.conversationRuntime,
    setConversationModel: assistant.setConversationModel,
    isActiveConversationRunning: assistant.isOpenedConversationRunning,
    isLoading: assistant.isLoading,
    isLoadingConversations: assistant.isLoadingConversations,
    isLoadingMessages: assistant.isLoadingMessages,
    isLoadingOlderMessages: assistant.isLoadingOlderMessages,
    hasOlderMessages: assistant.hasOlderMessages,
    isUploadingFiles: assistant.isUploadingFiles,
    pendingFiles: assistant.pendingFiles,
    pendingFileUploads: assistant.pendingFileUploads,
    error: assistant.error,
    canRetryFailedMessage: assistant.canRetryFailedMessage,
    pendingActions: assistant.pendingActions,
    completedActions: assistant.completedActions,
    streamingTool: assistant.streamingTool,
    selectConversation: assistant.selectConversation,
    sendMessage: assistant.sendMessage,
    retryFailedMessage: assistant.retryFailedMessage,
    uploadFiles: assistant.uploadFiles,
    removePendingFile: assistant.removePendingFile,
    clearPendingFiles: assistant.clearPendingFiles,
    loadOlderMessages: assistant.loadOlderMessages,
    resolveUserApproval: assistant.resolveUserApproval,
    clearMessages: assistant.clearMessages,
    stop: assistant.stop,
  };
}

function useAssistantPrefillDraft(setDraft: (value: string) => void) {
  const { clearMessages, openAssistant } = useAIAssistant();

  useEffect(() => {
    const handlePrefillEvent = (event: Event) => {
      const customEvent = event as CustomEvent<unknown>;
      const detail = parseAssistantPrefillDetail(customEvent.detail);
      if (!detail) return;

      if (detail.forceNewConversation) {
        clearMessages();
      }

      setDraft(detail.content);
      openAssistant();
    };

    window.addEventListener(ASSISTANT_PREFILL_EVENT, handlePrefillEvent);
    return () => {
      window.removeEventListener(ASSISTANT_PREFILL_EVENT, handlePrefillEvent);
    };
  }, [clearMessages, openAssistant, setDraft]);
}

function PodAssistantSurface({
  title,
  subtitle,
  placeholder,
  showConversationList,
  showModelPicker,
  composerModelControl,
  showNewConversationButton,
  showHeader,
  density,
  draft,
  onDraftChange,
  headerLeadingActions,
  headerActions,
  className,
  contentWidthClassName,
  composerWidthClassName,
  emptyState,
}: {
  title: ReactNode;
  subtitle: ReactNode;
  placeholder: string;
  showConversationList: boolean;
  showModelPicker: boolean;
  composerModelControl?: ReactNode;
  showNewConversationButton?: boolean;
  showHeader?: boolean;
  density?: "compact" | "comfortable" | "spacious";
  draft?: string;
  onDraftChange?: (value: string) => void;
  headerLeadingActions?: ReactNode;
  headerActions?: ReactNode;
  className?: string;
  contentWidthClassName?: string;
  composerWidthClassName?: string;
  emptyState?: ReactNode;
}) {
  const assistant = useAIAssistant();
  const mentionPodId = assistant.conversationPodId || assistant.podContext?.pod?.id;
  const { data: tablesData } = useTables(mentionPodId || undefined, DEFAULT_DATASTORE_NAME);
  const { data: filesData } = useDatastoreFiles(
    mentionPodId || undefined,
    DEFAULT_DATASTORE_NAME,
    { directory_path: "/", limit: 50 },
  );
  const controller = buildControllerView(assistant);
  const resourceMentions = useMemo<AssistantResourceMention[]>(() => {
    const tableMentions = (tablesData?.items || []).map((table) => ({
      id: `table:${table.name}`,
      kind: "table" as const,
      label: table.name,
      insertText: `@table:${table.name}`,
      detail: "Pod table",
    }));

    const seenFiles = new Set<string>();
    const fileMentions = ((filesData as { items?: unknown[] } | undefined)?.items || [])
      .map(getFileMentionPath)
      .filter((path) => {
        if (!path || seenFiles.has(path)) return false;
        seenFiles.add(path);
        return true;
      })
      .map((path) => ({
        id: `file:${path}`,
        kind: "file" as const,
        label: fileMentionLabel(path),
        insertText: `@file:${path}`,
        detail: path,
      }));

    return [...tableMentions, ...fileMentions];
  }, [filesData, tablesData?.items]);

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <AssistantExperienceView
        controller={controller}
        title={title}
        subtitle={subtitle}
        placeholder={placeholder}
        badge={null}
        showConversationList={showConversationList}
        showModelPicker={showModelPicker}
        composerModelControl={composerModelControl}
        showNewConversationButton={showNewConversationButton}
        showHeader={showHeader}
        appearance="minimal"
        density={density ?? "comfortable"}
        radius="xl"
        draft={draft}
        onDraftChange={onDraftChange}
        headerLeadingActions={headerLeadingActions}
        headerActions={headerActions}
        onNavigateResource={assistant.navigateToResource}
        resourceMentions={resourceMentions}
        className={`${className ?? ""} min-h-0 flex-1 min-w-0`}
        contentWidthClassName={contentWidthClassName}
        composerWidthClassName={composerWidthClassName}
        emptyState={emptyState}
        emptyStateSuggestions={
          assistant.hasPodContext
            ? [
                { text: "Summarize the state of this pod" },
                { text: "What should I build next?" },
                { text: "Review the latest errors and unblock me" },
              ]
            : [
                { text: "Show my recent pods" },
                { text: "What can you help me with?" },
                { text: "Help me plan a new pod" },
              ]
        }
      />
    </div>
  );
}

export function PodAssistant() {
  const assistant = useAIAssistant();
  const { closeAssistant, hasPodContext, isOpen } = assistant;
  const [draft, setDraft] = useState("");
  const [isMaximized, setIsMaximized] = useState(false);
  const [isHistoryOpen, setIsHistoryOpen] = useState(false);

  useAssistantPrefillDraft(setDraft);

  useEffect(() => {
    if (!isOpen) return;

    const previousBodyOverflow = document.body.style.overflow;
    const previousHtmlOverflow = document.documentElement.style.overflow;
    document.body.style.overflow = "hidden";
    document.documentElement.style.overflow = "hidden";

    return () => {
      document.body.style.overflow = previousBodyOverflow;
      document.documentElement.style.overflow = previousHtmlOverflow;
    };
  }, [isOpen]);

  const handleCloseAssistant = useCallback(() => {
    setIsMaximized(false);
    setIsHistoryOpen(false);
    closeAssistant();
  }, [closeAssistant]);

  useEffect(() => {
    if (!isOpen) return;

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        handleCloseAssistant();
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [handleCloseAssistant, isOpen]);

  if (!isOpen) return null;

  const headerActions = (
    <>
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => setIsHistoryOpen((prev) => !prev)}
        aria-label={
          isHistoryOpen
            ? "Hide conversation history"
            : "Show conversation history"
        }
        title={
          isHistoryOpen
            ? "Hide conversation history"
            : "Show conversation history"
        }
        className="h-9 gap-2 px-3"
      >
        {isHistoryOpen ? (
          <PanelLeftClose className="h-4 w-4" />
        ) : (
          <PanelLeftOpen className="h-4 w-4" />
        )}
        <span className="hidden sm:inline">
          {isHistoryOpen ? "Hide" : "History"}
        </span>
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="icon"
        onClick={() => setIsMaximized((prev) => !prev)}
        aria-label={
          isMaximized
            ? "Exit fullscreen Lemma Assist"
            : "Enter fullscreen Lemma Assist"
        }
        title={isMaximized ? "Exit fullscreen" : "Fullscreen"}
        className="h-9 w-9"
      >
        {isMaximized ? (
          <Minimize2 className="h-4 w-4" />
        ) : (
          <Maximize2 className="h-4 w-4" />
        )}
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="icon"
        onClick={() => {
          handleCloseAssistant();
        }}
        aria-label="Close Lemma Assist"
        title="Close Lemma Assist"
        className="h-9 w-9"
      >
        <X className="h-4 w-4" />
      </Button>
    </>
  );

  return (
    <div
      className={`fixed inset-0 z-[80] flex justify-center ${isMaximized ? "bg-[var(--bg-canvas)] p-0" : "scrim-overlay-subtle p-0 backdrop-blur-[1px] sm:items-center sm:p-4"}`}
      onClick={(event) => {
        if (event.target !== event.currentTarget) return;
        handleCloseAssistant();
      }}
    >
      <div
        className={`relative w-full ${isMaximized ? "h-full" : "h-full sm:h-[min(82vh,860px)] sm:w-[min(96vw,1120px)]"}`}
        onClick={(event) => event.stopPropagation()}
      >
        <PodAssistantSurface
          title="Lemma Assist"
          subtitle={
            hasPodContext
              ? "Plan, build, and update your pod."
              : "Ask across your workspace and organization."
          }
          placeholder="Message Lemma Assist"
          showConversationList={isHistoryOpen}
          showModelPicker
          showNewConversationButton
          draft={draft}
          onDraftChange={setDraft}
          headerActions={headerActions}
          className={`h-full ${isMaximized ? "rounded-none border-0 bg-[var(--card-bg)]" : "rounded-none border-0 bg-[var(--card-bg)] sm:rounded-lg sm:border sm:border-[var(--card-border)] sm:shadow-[var(--shadow-lg)]"}`}
        />
      </div>
    </div>
  );
}

export function PodAssistantSidebar({
  presentationMode = false,
  onClose,
  presentation = "closed",
  dockWidth = DEFAULT_ASSISTANT_DOCK_WIDTH,
  onDockWidthChange,
}: {
  presentationMode?: boolean;
  onClose?: () => void;
  presentation?: AssistantPresentation;
  dockWidth?: AssistantDockWidth;
  onDockWidthChange?: (width: AssistantDockWidth) => void;
}) {
  const assistant = useAIAssistant();
  const {
    closeAssistant,
    hasPodContext,
  } = assistant;
  const router = useRouter();
  const [draft, setDraft] = useState("");
  const isVisible = presentation !== "closed";
  const isOverlay = presentation === "overlay";
  const isDocked = presentation === "docked";
  const resizeStartRef = useRef({
    pointerId: 0,
    startX: 0,
    width: dockWidth as number,
  });
  useAssistantPrefillDraft(setDraft);
  const activeConversation = useMemo(() => {
    const openedConversationId = assistant.openedConversationId;
    if (!openedConversationId) return null;
    return assistant.conversations.find((conversation) => conversation.id === openedConversationId) ?? null;
  }, [assistant.conversations, assistant.openedConversationId]);
  const assistantTitle = activeConversation?.title?.trim() || (assistant.openedConversationId ? "Untitled conversation" : "New conversation");
  const activeStatusView = getConversationStatusView(activeConversation?.status);

  const handleCollapseAssistant = useCallback(() => {
    if (onClose) {
      onClose();
      return;
    }
    closeAssistant();
  }, [closeAssistant, onClose]);

  const openConversationPage = useCallback(() => {
    const podId = assistant.conversationPodId || assistant.podContext?.pod?.id;
    if (!podId) return;
    const conversationId = assistant.openedConversationId;
    closeAssistant({ skipUrlSync: true });
    router.push(conversationId
      ? `/pod/${podId}/conversations/${encodeURIComponent(conversationId)}`
      : `/pod/${podId}/conversations/new`);
  }, [assistant.conversationPodId, assistant.openedConversationId, assistant.podContext?.pod?.id, closeAssistant, router]);

  // Resize only applies to the docked panel; the overlay is a fixed-width float.
  const handleResizePointerDown = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      if (!isDocked) return;

      event.preventDefault();
      resizeStartRef.current = {
        pointerId: event.pointerId,
        startX: event.clientX,
        width: dockWidth,
      };
      event.currentTarget.setPointerCapture(event.pointerId);
    },
    [dockWidth, isDocked],
  );

  const handleResizePointerMove = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      if (resizeStartRef.current.pointerId !== event.pointerId) return;

      const delta = event.clientX - resizeStartRef.current.startX;
      onDockWidthChange?.(
        closestAssistantDockWidth(resizeStartRef.current.width + delta),
      );
    },
    [onDockWidthChange],
  );

  const handleResizePointerUp = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      if (resizeStartRef.current.pointerId !== event.pointerId) return;
      resizeStartRef.current.pointerId = 0;
      event.currentTarget.releasePointerCapture(event.pointerId);
    },
    [],
  );

  const headerActions = presentationMode ? null : (
    <>
      <Button
        type="button"
        variant="ghost"
        size="icon"
        onClick={openConversationPage}
        aria-label="Open conversation page"
        title="Open conversation page"
        className="h-8 w-8"
      >
        <Maximize2 className="h-4 w-4" />
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="icon"
        onClick={handleCollapseAssistant}
        aria-label="Close Lemma Assist"
        title="Close"
        className="h-8 w-8"
      >
        <X className="h-4 w-4" />
      </Button>
    </>
  );

  return (
    <>
      {isOverlay ? (
        <button
          type="button"
          className="pod-assistant-scrim-button scrim-overlay-subtle fixed inset-0 z-40"
          onClick={handleCollapseAssistant}
          aria-label="Collapse Lemma Assist"
        />
      ) : null}

      <aside
        /* eslint-disable-next-line no-restricted-syntax -- assistant width is runtime geometry (resizable dock width / overlay clamp) */
        style={{ width: isVisible ? `min(100vw, ${dockWidth}px)` : 0 }}
        className={cn(
          "pod-assistant-sidebar flex flex-col overflow-hidden bg-[var(--pod-main-bg)]",
          isDocked && "relative z-20 h-full min-h-0 shrink-0 self-stretch border-r border-[color:color-mix(in_srgb,var(--border-subtle)_32%,transparent)] shadow-none",
          isOverlay && "fixed bottom-0 right-0 top-0 z-50 h-full shadow-[var(--shadow-lg)]",
          isVisible ? "opacity-100" : "pointer-events-none opacity-0",
        )}
        aria-label="Lemma Assist"
        aria-hidden={!isVisible}
      >
        <PodAssistantSurface
          title={assistantTitle}
          subtitle={hasPodContext ? "Lemma Assist" : "Workspace assist"}
          placeholder="Message Lemma Assist"
          showConversationList={false}
          showModelPicker={false}
          showNewConversationButton={false}
          density="compact"
          draft={draft}
          onDraftChange={setDraft}
          headerActions={(
            <>
              <ConversationStatusPill statusView={activeStatusView} />
              {headerActions}
            </>
          )}
          className="pod-assistant-sidebar-surface h-full min-h-0 rounded-none border-0 bg-transparent shadow-none"
        />
        {isDocked ? (
          <div
            role="separator"
            aria-label="Resize Lemma Assist"
            aria-orientation="vertical"
            className="absolute right-0 top-0 hidden h-full w-1 cursor-col-resize touch-none bg-transparent transition-colors hover:bg-[var(--delight)] md:block"
            onPointerDown={handleResizePointerDown}
            onPointerMove={handleResizePointerMove}
            onPointerUp={handleResizePointerUp}
            onPointerCancel={handleResizePointerUp}
          />
        ) : null}
      </aside>
    </>
  );
}

export function PodAssistantEmbedded({
  title = "Lemma Assist",
  subtitle = "Chat with Lemma Assist in the current pod.",
  placeholder = "Message Lemma Assist...",
  className,
  density = "comfortable",
  showHeader = true,
  showModelPicker = false,
  composerModelControl,
  showNewConversationButton = true,
  showConversationList = false,
  contentWidthClassName,
  composerWidthClassName,
  emptyState,
  draft,
  onDraftChange,
}: PodAssistantEmbeddedProps = {}) {
  return (
    <div className="h-full min-h-0">
      <PodAssistantSurface
        title={title}
        subtitle={subtitle}
        placeholder={placeholder}
        showConversationList={showConversationList}
        showModelPicker={showModelPicker}
        composerModelControl={composerModelControl}
        showNewConversationButton={showNewConversationButton}
        contentWidthClassName={contentWidthClassName}
        composerWidthClassName={composerWidthClassName}
        emptyState={emptyState}
        draft={draft}
        onDraftChange={onDraftChange}
        density={density}
        showHeader={showHeader}
        className={cn(
          "h-full rounded-none border-0 bg-transparent shadow-none",
          className,
        )}
      />
    </div>
  );
}
