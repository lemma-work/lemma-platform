import type { ReactNode } from "react";
import type { AgentRuntimeConfig, AvailableModelInfo } from "lemma-sdk";
import type {
  AssistantAction,
  AssistantPendingFileUpload,
  AssistantRenderableMessage,
  AssistantStreamingTool,
  AssistantToolInvocation,
} from "lemma-sdk/react";

export type LemmaAssistantAppearance = "default" | "minimal" | "borderless" | "contained";
export type LemmaAssistantDensity = "compact" | "comfortable" | "spacious";
export type LemmaAssistantRadius = "none" | "sm" | "md" | "lg" | "xl";

export interface AssistantConversationListItem {
  id: string;
  title?: string | null;
  status?: string | null;
  updated_at?: string | null;
  created_at?: string | null;
}

export interface AssistantControllerView {
  messages: Array<AssistantRenderableMessage & {
    metadata?: Record<string, unknown> | null;
    message_metadata?: Record<string, unknown> | null;
    tool_name?: string | null;
    tool_call_id?: string | null;
  }>;
  conversations: AssistantConversationListItem[];
  activeConversationId: string | null;
  availableModels: AvailableModelInfo[];
  conversationModel: string | null;
  conversationRuntime?: AgentRuntimeConfig | null;
  setConversationModel(model: string | null, runtime?: AgentRuntimeConfig | null): Promise<void>;
  isActiveConversationRunning: boolean;
  isLoading: boolean;
  isLoadingConversations: boolean;
  isLoadingMessages: boolean;
  isLoadingOlderMessages: boolean;
  hasOlderMessages: boolean;
  isUploadingFiles: boolean;
  pendingFiles: File[];
  pendingFileUploads?: AssistantPendingFileUpload[];
  error: Error | string | null;
  canRetryFailedMessage?: boolean;
  pendingActions: AssistantAction[];
  completedActions: AssistantAction[];
  streamingTool?: AssistantStreamingTool | null;
  selectConversation(conversationId: string | null): void;
  sendMessage(content: string, options?: { forceNewConversation?: boolean }): Promise<void>;
  retryFailedMessage?(): Promise<void>;
  uploadFiles(files: File[], options?: { deferUntilSend?: boolean }): Promise<void>;
  removePendingFile(fileKey: string): void;
  clearPendingFiles(): void;
  loadOlderMessages(): Promise<boolean>;
  resolveUserApproval?: (
    approvalId: string,
    decision: "APPROVE_ONCE" | "APPROVE_FOR_SESSION" | "DENY",
    response?: Record<string, unknown> | null,
  ) => Promise<void>;
  clearMessages(): void;
  stop(): void;
}

export interface AssistantConversationRenderArgs {
  conversation: AssistantConversationListItem;
  isActive: boolean;
}

export interface AssistantMessageRenderArgs {
  message: AssistantRenderableMessage;
}

export interface AssistantToolRenderArgs {
  invocation: AssistantToolInvocation;
  message: AssistantRenderableMessage;
  activeConversationId: string | null;
}

export interface AssistantPendingFileRenderArgs {
  file: File;
  remove: () => void;
  status?: AssistantPendingFileUpload["status"];
  path?: string;
  error?: string;
}

export interface AssistantFinalOutputRenderArgs {
  output: Record<string, unknown>;
  schema?: unknown;
}

export interface EmptyStateSuggestion {
  text: string;
  icon?: ReactNode;
}

export interface AssistantResourceMention {
  id: string;
  kind: "file" | "table";
  label: string;
  insertText: string;
  detail?: string;
}

export interface AssistantExperienceCustomizationProps {
  className?: string;
  contentWidthClassName?: string;
  composerWidthClassName?: string;
  title?: ReactNode;
  subtitle?: ReactNode;
  badge?: ReactNode | null;
  headerLeadingActions?: ReactNode;
  headerActions?: ReactNode;
  composerModelControl?: ReactNode;
  placeholder?: string;
  emptyState?: ReactNode;
  emptyStateSuggestions?: EmptyStateSuggestion[];
  resourceMentions?: AssistantResourceMention[];
  draft?: string;
  onDraftChange?: (value: string) => void;
  showConversationList?: boolean;
  renderConversationLabel?: (args: AssistantConversationRenderArgs) => ReactNode;
  renderMessageContent?: (args: AssistantMessageRenderArgs) => ReactNode;
  renderToolInvocation?: (args: AssistantToolRenderArgs) => ReactNode;
  renderPendingFile?: (args: AssistantPendingFileRenderArgs) => ReactNode;
  finalOutput?: Record<string, unknown> | null;
  outputSchema?: unknown;
  showFinalOutput?: boolean;
  renderFinalOutput?: (args: AssistantFinalOutputRenderArgs) => ReactNode;
}
