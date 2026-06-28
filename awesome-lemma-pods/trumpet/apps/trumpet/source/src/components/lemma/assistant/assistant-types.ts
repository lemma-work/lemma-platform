import type { ReactNode } from "react";
import type { AvailableModelInfo } from "lemma-sdk";
import type {
  AssistantAction,
  AssistantRenderableMessage,
  AssistantToolInvocation,
  SendAssistantControllerMessageOptions,
} from "lemma-sdk/react";

export type LemmaAssistantAppearance = "default" | "minimal" | "borderless" | "contained";
export type LemmaAssistantDensity = "compact" | "comfortable" | "spacious";
export type LemmaAssistantRadius = "none" | "sm" | "md" | "lg" | "xl";
export type LemmaAssistantMode = "page" | "embedded" | "side-panel" | "popup";
export type AssistantPopupPosition = "bottom-right" | "bottom-left" | "top-right" | "top-left";
export type AssistantPopupTriggerVariant = "icon" | "pill";
export type AssistantLaunchContextKind = "record" | "file" | "table" | "search_result" | "page";

export interface AssistantConversationListItem {
  id: string;
  title?: string | null;
  status?: string | null;
  updated_at?: string | null;
  created_at?: string | null;
}

export interface AssistantControllerView {
  messages: AssistantRenderableMessage[];
  conversations: AssistantConversationListItem[];
  activeConversationId: string | null;
  availableModels: AvailableModelInfo[];
  conversationModel: string | null;
  setConversationModel(model: string | null): Promise<void>;
  isActiveConversationRunning: boolean;
  isLoading: boolean;
  isLoadingConversations: boolean;
  isLoadingMessages: boolean;
  isLoadingOlderMessages: boolean;
  hasOlderMessages: boolean;
  isUploadingFiles: boolean;
  pendingFiles: File[];
  error: Error | string | null;
  pendingActions: AssistantAction[];
  completedActions: AssistantAction[];
  selectConversation(conversationId: string | null): void;
  sendMessage(content: string, options?: SendAssistantControllerMessageOptions): Promise<void>;
  uploadFiles(files: File[], options?: { deferUntilSend?: boolean }): Promise<void>;
  removePendingFile(fileKey: string): void;
  clearPendingFiles(): void;
  loadOlderMessages(): Promise<boolean>;
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

export interface AssistantPresentedFileRenderArgs extends AssistantToolRenderArgs {
  filepath: string;
}

export interface AssistantPendingFileRenderArgs {
  file: File;
  remove: () => void;
}

export interface EmptyStateSuggestion {
  text: string;
  icon?: ReactNode;
}

export interface AssistantLaunchContextItem {
  kind: AssistantLaunchContextKind;
  title: ReactNode;
  description?: ReactNode;
  meta?: ReactNode;
  prompt?: string;
  actionLabel?: ReactNode;
  href?: string;
  onSelect?: () => void;
}

export interface AssistantExperienceCustomizationProps {
  className?: string;
  title?: ReactNode;
  subtitle?: ReactNode;
  badge?: ReactNode | null;
  placeholder?: string;
  emptyState?: ReactNode;
  emptyStateSuggestions?: EmptyStateSuggestion[];
  draft?: string;
  onDraftChange?: (value: string) => void;
  showConversationList?: boolean;
  renderConversationLabel?: (args: AssistantConversationRenderArgs) => ReactNode;
  renderMessageContent?: (args: AssistantMessageRenderArgs) => ReactNode;
  renderToolInvocation?: (args: AssistantToolRenderArgs) => ReactNode;
  renderPresentedFile?: (args: AssistantPresentedFileRenderArgs) => ReactNode;
  renderPendingFile?: (args: AssistantPendingFileRenderArgs) => ReactNode;
  launchContext?: AssistantLaunchContextItem | AssistantLaunchContextItem[];
  popupOpen?: boolean;
  defaultPopupOpen?: boolean;
  onPopupOpenChange?: (open: boolean) => void;
  popupPosition?: AssistantPopupPosition;
  popupTriggerLabel?: string;
  popupTriggerIcon?: ReactNode;
  popupTriggerVariant?: AssistantPopupTriggerVariant;
}
