import type {
  AgentRuntimeConfig,
  AgentHarnessInfo,
  AgentHarnessListResponse,
  AgentDetailResponse,
  AgentSummaryResponse,
  AgentRuntimeProfileListResponse,
  AgentRuntimeProfileResponse,
  ColumnSchema,
  ConversationResponse as GeneratedConversationResponse,
  CreateAgentRequest,
  DatastoreQueryResponse,
  DirectoryTreeNode,
  DirectoryTreeResponse,
  FileDetailResponse,
  FileSummaryResponse,
  FileSearchResponse,
  FileSearchResultSchema,
  WorkflowDetailResponse,
  WorkflowSummaryResponse,
  TableSummaryResponse,
  WorkflowRunResponse,
  FunctionDetailResponse,
  FunctionRunResponse,
  FunctionRunSummaryResponse,
  HarnessKind,
  IconUploadResponse,
  OrganizationInvitationResponse,
  OrganizationMemberResponse,
  OrganizationResponse,
  PodJoinRequestCreateResponse,
  PodMemberResponse,
  PodRoleResponse,
  PodResponse,
  ResourceAccessGrantResponse,
  ResourceAccessResponse,
  ResourceType as GeneratedResourceType,
  ScheduleDetailResponse,
  TableDetailResponse,
  UpdateAgentRequest,
  UsageRecordResponse as GeneratedUsageRecordResponse,
  UsageListResponse as GeneratedUsageListResponse,
  UsageStatsBucketResponse as GeneratedUsageStatsBucketResponse,
  UsageStatsResponse as GeneratedUsageStatsResponse,
  UsageSummaryResponse as GeneratedUsageSummaryResponse,
  UserResponse,
  WorkflowRunWaitAssignment,
  WorkflowRunSummaryResponse,
} from "./openapi_client/index.js";

/** Public ergonomic types. */

export interface AvailableModelInfo {
  id: ConversationModel;
  name: string;
  runtime?: AgentRuntimeConfig;
  agentRuntime?: AgentRuntimeResponse;
  agentRuntimeId?: string;
  profile?: AgentRuntimeProfileResponse;
  profile_id?: string;
  harness_kind?: HarnessKind;
  description?: string | null;
}

export type AvailableModels = ConversationModel;
export type AgentRuntime = AgentRuntimeConfig;
export type AgentRuntimeAvailability = AgentHarnessInfo;
export type AgentRuntimeAvailabilityList = AgentHarnessListResponse;
export type AgentRuntimeListResponse = AgentRuntimeProfileListResponse;
export type AgentRuntimeResponse = AgentRuntimeProfileResponse;

export interface PageResult<T> {
  items: T[];
  nextPageToken?: string;
  total?: number;
}

export interface RecordFilter {
  field: string;
  op: string;
  value?: unknown;
  values?: unknown[];
}

export interface RecordSort {
  field: string;
  direction?: "asc" | "desc" | string;
}

export interface ListRecordsOptions {
  filters?: RecordFilter[];
  sort?: RecordSort[];
  limit?: number;
  pageToken?: string;
  offset?: number;
}

export interface RunFunctionOptions {
  /** Input payload for the function */
  input?: Record<string, unknown>;
}

/** Form field values submitted to a workflow form node. */
export interface WorkflowRunInputs {
  [key: string]: unknown;
}

export interface StreamOptions {
  signal?: AbortSignal;
}

/** Ergonomic entity aliases (instead of *Response/*Request names). */
export type Agent = AgentDetailResponse;
/** Lean agent shape returned by list endpoints (no instruction/schemas/runtime). */
export type AgentSummary = AgentSummaryResponse;
export type CreateAgentInput = CreateAgentRequest;
export type UpdateAgentInput = UpdateAgentRequest;

export type ConversationModel = string & {};
export type Conversation = GeneratedConversationResponse & {
  model?: ConversationModel | null;
  status?: string | null;
};

/** Discriminator for the flat message shape (replaces the old nested content union). */
export type MessageKind =
  | "TEXT"
  | "THINKING"
  | "NOTIFICATION"
  | "TOOL_CALL"
  | "TOOL_RETURN";

export interface ConversationMessageResponse {
  id: string;
  role: string;
  kind: MessageKind;
  /** Body for text / thinking / notification messages. */
  text?: string | null;
  /** Set on tool_call / tool_return messages. */
  tool_name?: string | null;
  tool_call_id?: string | null;
  /** Inputs for a tool_call (arbitrary JSON). */
  tool_args?: unknown;
  /** Output for a tool_return (arbitrary JSON). */
  tool_result?: unknown;
  created_at: string;
  conversation_id?: string;
  sequence?: number;
  agent_run_id?: string | null;
  metadata?: Record<string, unknown> | null;
}

export type ConversationMessage = ConversationMessageResponse;

export type FunctionRun = FunctionRunResponse;
export type FunctionRunSummary = FunctionRunSummaryResponse;
export type FlowRun = WorkflowRunResponse;
export type WorkflowRunSummary = WorkflowRunSummaryResponse;
export type Workflow = WorkflowDetailResponse;
/** Lean workflow shape returned by list endpoints (no graph; node_count/node_types only). */
export type WorkflowSummary = WorkflowSummaryResponse;
export type WorkflowStart = Workflow["start"];
export type WorkflowStartType = NonNullable<WorkflowStart>["type"];
export type Schedule = ScheduleDetailResponse;
export type WorkflowRunWait = WorkflowRunWaitAssignment;
/** @deprecated Use Table or TableDetailResponse. */
export type TableResponse = TableDetailResponse;
export type Table = TableDetailResponse;
/** Lean table shape returned by list endpoints (no columns/config; column_count only). */
export type TableSummary = TableSummaryResponse;
export type TableColumn = ColumnSchema;
export type DatastoreQueryResult = DatastoreQueryResponse;
/** @deprecated Use DatastoreFile or FileDetailResponse. */
export type FileResponse = FileDetailResponse;
export type DatastoreFile = FileDetailResponse;
/** Lean file/folder shape returned by list endpoints (no file_metadata/last_processing_error). */
export type DatastoreFileSummary = FileSummaryResponse;
export type DatastoreFileSearchResponse = FileSearchResponse;
export type DatastoreFileSearchResult = FileSearchResultSchema;
export type DatastoreDirectoryTree = DirectoryTreeResponse;
export type DatastoreDirectoryTreeNode = DirectoryTreeNode;

export type Pod = PodResponse;
export type PodMember = PodMemberResponse;
export type PodJoinRequest = PodJoinRequestCreateResponse;
export type PodRoleInfo = PodRoleResponse;
export type ResourceAccess = ResourceAccessResponse;
export type ResourceAccessGrant = ResourceAccessGrantResponse;
export type ResourceAccessType = GeneratedResourceType;

export type Organization = OrganizationResponse;
export type OrganizationMember = OrganizationMemberResponse;
export type OrganizationInvitation = OrganizationInvitationResponse;

export type User = UserResponse;
export type UploadedIcon = IconUploadResponse;
/** @deprecated Use FunctionDetailResponse. */
export type FunctionResponse = FunctionDetailResponse;
/** @deprecated Use UsageListResponse for recent usage events. */
export type RecentUsageResponse = Omit<GeneratedUsageListResponse, "items"> & {
  items: UsageRecordResponse[];
};
export type UsageSummaryResponse = GeneratedUsageSummaryResponse;
export type UsageStatsBucketResponse = GeneratedUsageStatsBucketResponse;
export type UsageStatsResponse = Omit<GeneratedUsageStatsResponse, "items"> & {
  items: UsageStatsBucketResponse[];
};
export type UsageRecordResponse = GeneratedUsageRecordResponse;
export type {
  AuthConfigCreateSchema,
  AuthConfigListResponseSchema,
  AuthConfigResponseSchema,
} from "./namespaces/connectors.js";

/** Generic cursor-style page shape used by many list endpoints. */
export interface CursorPage<T> {
  items: T[];
  limit: number;
  next_page_token?: string | null;
  total?: number;
}

/**
 * Re-export generated OpenAPI models/enums/services from the same module so this
 * file remains the single public type surface for the SDK.
 */
export * from "./openapi_client/index.js";
