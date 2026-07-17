export { LemmaClient } from "./client.js";
export type { LemmaAppConfig, LemmaConfig } from "./client.js";
export {
  AuthManager,
  buildAuthUrl,
  buildFederatedLogoutUrl,
  clearTestingToken,
  getTestingToken,
  resolveSafeRedirectUri,
  setTestingToken,
} from "./auth.js";
export type {
  AuthState,
  AuthListener,
  AuthStatus,
  UserInfo,
  AuthRedirectMode,
  BuildAuthUrlOptions,
  BuildFederatedLogoutUrlOptions,
  RedirectToFederatedLogoutOptions,
  ResolveSafeRedirectUriOptions,
} from "./auth.js";
export {
  ApiError,
  UnauthorizedError,
  ForbiddenError,
  NotFoundError,
  ConflictError,
  RateLimitError,
  ServerError,
  NetworkError,
  apiErrorFromStatus,
} from "./http.js";
export * from "./types.js";
export { readSSE, parseSSEJson } from "./streams.js";
export type { SseRawEvent } from "./streams.js";
export { watchDatastoreChanges } from "./datastore-changes.js";
export type {
  DatastoreChangeFrame,
  ChangeStreamStatus,
  ChangeStreamHandle,
  ChangeStreamTokenProvider,
  WatchChangesOptions,
} from "./datastore-changes.js";
export {
  normalizeRunStatus,
  isTerminalFunctionStatus,
  isTerminalWorkflowStatus,
  sleep,
  nextBackoffDelay,
} from "./run-utils.js";
export type { AnyRunStatus } from "./run-utils.js";
export { parseAssistantStreamEvent, upsertConversationMessage } from "./assistant-events.js";
export type { ParsedAssistantStreamEvent } from "./assistant-events.js";
export { normalizeAgentToolName } from "./core/agent/tool-names.js";
// Framework-agnostic agent core (drives the React hooks and, next, web components).
export {
  AgentController,
  selectAgentOutputs,
  selectAgentTask,
  agentActivityLabel,
  conversationMessageText,
  extractConversationMessageText,
  getLatestAssistantMessage,
  isConversationRunningStatus,
  normalizeConversationStatus,
  sortConversationMessagesByCreatedAt,
  extractAgentFinalOutput,
  extractMessageText,
  extractJsonObject,
  latestAssistantText,
  isFinalResultToolName,
  unwrapFinalResultPayload,
  buildDisplayMessageRows,
  dedupToolInvocations,
  collectCompletedRunTraceGroups,
  findPendingUserApprovalInvocation,
  isToolInvocationActive,
  isUserApprovalToolName,
  isAskUserToolName,
  isUserInteractionToolName,
  isRenderableUserInteractionInvocation,
  userApprovalResolvedDecision,
  latestPlanSummary,
  latestUserIndex,
  messageTextContent,
  normalizeAssistantMarkdown,
  formatDurationCompact,
  messageTimeMs,
  toolInvocationKey,
  preferToolInvocation,
  isLongRunningToolResult,
  messageRecord,
  contentRecord,
  metadataRecord,
  messageAgentRunId,
  messageFlag,
  isFinalAnswerMessage,
  isIntermediateAssistantMessage,
  normalizeAssistantDisplayText,
  hasMeaningfulTextPart,
  toolMessageKind,
  toolMessageName,
  toolMessageCallId,
  toolMessageInput,
  toolMessageOutput,
  messageHasToolActivity,
  finalAnswerRunIds,
  shouldConvertMessageToTraceNote,
  shouldFoldIntermediateMessage,
  completedTurnTraceDurations,
  messageWithTraceNote,
  messageWithMergedToolResult,
  messageWithRawToolCall,
  isCollapsibleAssistantMessage,
  assistantMessageHasRenderableContent,
  prepareMessagesForDisplay,
  isRunClosingMessage,
  completedRunLabel,
  rowSourceTimesMs,
  rowIsAfterIndex,
} from "./core/agent/index.js";
export type {
  AgentControllerOptions,
  AgentSessionState,
  AgentOutputs,
  AgentTaskView,
  AgentTaskStatus,
  AgentFinalOutput,
  MessageLike,
  ToolInvocationLike,
  MessagePartLike,
  AssistantToolInvocation,
  AssistantMessagePart,
  AssistantRenderableMessage,
  DisplayMessageRow,
  CompletedRunTraceGroupState,
  PlanStatus,
  PlanStepState,
  PlanSummaryState,
  AssistantStreamingTool,
  ConversationScope,
  CreateConversationInput,
  ResumeAssistantOptions,
  SendAssistantMessageOptions,
} from "./core/agent/index.js";
// Framework-agnostic workflow core (controller + helpers; no baked-in UI).
export {
  WorkflowController,
  selectWorkflowOutputs,
  getRunInputFields,
  buildWorkflowFormSubmit,
  shouldPollWorkflowRun,
} from "./core/workflow/index.js";
export type {
  WorkflowControllerOptions,
  WorkflowSessionState,
  WorkflowOutputs,
  WorkflowScope,
  WorkflowStartOptions,
  WorkflowResumeOptions,
} from "./core/workflow/index.js";
// Vanilla web components for no-build HTML apps (Node/SSR-safe to import).
export {
  defineLemmaElements,
  LemmaAgentTaskElement,
  LemmaAgentThreadElement,
} from "./ui/index.js";
export {
  DEFAULT_RECORD_FORM_HIDDEN_FIELDS,
  buildRecordFormValues,
  buildRecordPayload,
  buildRecordSchemaFields,
  formatRecordValueForForm,
  getEditableRecordFields,
  getRecordFieldKind,
  orderRecordSchemaFields,
} from "./record-form.js";
export type {
  BuildRecordPayloadOptions,
  BuildRecordPayloadResult,
  RecordSchemaField,
  RecordSchemaFieldKind,
} from "./record-form.js";
export {
  buildDefaultRecordDetailFieldGroups,
  detectRecordDescriptionColumn,
  detectRecordStatusColumn,
  detectRecordTitleColumn,
  formatRecordDateDisplayValue,
  formatRecordDisplayValue,
  formatRecordPlainValue,
  humanizeRecordFieldName,
  isDefaultRecordDetailHiddenField,
} from "./record-display.js";
export type { RecordDetailFieldGroupDefinition } from "./record-display.js";
export {
  buildSchemaFormFields,
  buildSchemaFormPayload,
  buildSchemaFormValues,
  formatSchemaFieldValueForForm,
} from "./schema-form.js";
export type {
  BuildSchemaFormPayloadResult,
  JsonSchemaLike,
  JsonSchemaPrimitiveType,
  SchemaFormField,
  SchemaFormFieldKind,
} from "./schema-form.js";
export {
  buildJoinedRecordsQuery,
  parseForeignKeyReference,
} from "./datastore-query.js";
export type {
  ForeignKeyReference,
  JoinedRecordsColumnRef,
  JoinedRecordsFilter,
  JoinedRecordsJoin,
  JoinedRecordsJoinCondition,
  JoinedRecordsOrderBy,
  JoinedRecordsQueryDefinition,
  JoinedRecordsSelectField,
  JoinedRecordsSource,
} from "./datastore-query.js";

export { POD_DEFAULT_AGENT_SELECTOR } from "./namespaces/conversations.js";

// Namespace types (for advanced usage)
export type { AgentRuntimeNamespace } from "./namespaces/agent-runtime.js";
export type { AgentsNamespace } from "./namespaces/agents.js";
export type { DatastoreNamespace } from "./namespaces/datastore.js";
export type { ConversationsNamespace } from "./namespaces/conversations.js";
export type { AppsNamespace } from "./namespaces/apps.js";
export type { FilesNamespace } from "./namespaces/files.js";
export type { FunctionsNamespace } from "./namespaces/functions.js";
export type { IconsNamespace } from "./namespaces/icons.js";
export type { ConnectorsNamespace } from "./namespaces/connectors.js";
export type { OrganizationsNamespace } from "./namespaces/organizations.js";
export type { PodJoinRequestsNamespace } from "./namespaces/pod-join-requests.js";
export type { PodMembersNamespace } from "./namespaces/pod-members.js";
export type { PodPermissionsNamespace } from "./namespaces/pod-permissions.js";
export type { PodRolesNamespace } from "./namespaces/pod-roles.js";
export type { PodsNamespace } from "./namespaces/pods.js";
export type { PodSurfacesNamespace } from "./namespaces/pod-surfaces.js";
export type { UserSurfacesNamespace } from "./namespaces/user-surfaces.js";
export type { RecordsNamespace } from "./namespaces/records.js";
export type { ResourceAccessNamespace } from "./namespaces/resource-access.js";
export type { ScheduleListOptions, SchedulesNamespace } from "./namespaces/schedules.js";
export type { TablesNamespace } from "./namespaces/tables.js";
export type { UsersNamespace } from "./namespaces/users.js";
export type { WorkflowsNamespace } from "./namespaces/workflows.js";
