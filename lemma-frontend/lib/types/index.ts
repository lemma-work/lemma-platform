import {
  AgentToolset,
  DatastoreDataType,
  FunctionRunStatus,
  FunctionStatus,
  OrganizationInvitationStatus,
  OrganizationJoinPolicy,
  OrganizationRole,
  PodJoinPolicy,
  PodRole,
  ScheduleType,
} from 'lemma-sdk';

import type {
  AccountResponseSchema as SdkAccount,
  Agent as SdkAgent,
  AgentPermissionsResponse as SdkAgentPermissions,
  AgentRuntimeConfig as SdkAgentRuntimeConfig,
  AgentSurfaceResponse as SdkAssistantSurface,
  ConnectorResponseSchema as SdkConnector,
  AuthConfigResponseSchema as SdkAuthConfig,
  ColumnSchema as SdkColumn,
  Conversation as SdkConversation,
  ConversationMessage as SdkConversationMessage,
  ConversationModel as SdkConversationModel,
  CreateAgentInput as SdkCreateAgentInput,
  CreateFunctionRequest as SdkCreateFunctionRequest,
  DataStoreWorkflowStartInput as SdkDatastoreEventFlowStart,
  EventWorkflowStartInput as SdkEventFlowStart,
  DatastoreFile as SdkFileResponse,
  WorkflowDetailResponse as SdkFlow,
  WorkflowRunResponse as SdkFlowRun,
  WorkflowStart as SdkFlowStart,
  WorkflowStartType as SdkFlowStartType,
  FunctionResponse as SdkFunction,
  FunctionPermissionsResponse as SdkFunctionPermissions,
  FunctionRunResponse as SdkFunctionRun,
  MessageResponseSchema as SdkMessageResponse,
  OrganizationSlugAvailabilityResponse as SdkOrganizationSlugAvailabilityResponse,
  OrganizationMessageResponse as SdkOrganizationMessageResponse,
  Organization as SdkOrganization,
  OrganizationInvitation as SdkOrganizationInvitation,
  OrganizationMember as SdkOrganizationMember,
  Pod as SdkPod,
  PodCreateRequest as SdkCreatePodInput,
  PodMember as SdkPodMember,
  PodUpdateRequest as SdkUpdatePodInput,
  DatastoreFileSummary as SdkFileInfo,
  Schedule as SdkSchedule,
  CreateScheduleRequest as SdkCreateScheduleRequest,
  ScheduledWorkflowStartInput as SdkScheduledFlowStart,
  TableResponse as SdkTable,
  RecentUsageResponse as SdkRecentUsageResponse,
  UpdateAgentInput as SdkUpdateAgentInput,
  UpdateFunctionRequest as SdkUpdateFunctionRequest,
  UploadedIcon as SdkUploadedIcon,
  User as SdkUser,
  UpdateScheduleRequest as SdkUpdateScheduleRequest,
  UsageLimitsResponse as SdkUsageLimitsResponse,
  UsageListResponse as SdkUsageListResponse,
  UsageRecordResponse as SdkUsageRecordResponse,
  UsageStatsResponse as SdkUsageStatsResponse,
  UsageSummaryResponse as SdkUsageSummaryResponse,
  WorkflowCreateRequest as SdkWorkflowCreateRequest,
  WorkflowEdge as SdkWorkflowEdge,
  WorkflowGraphUpdateRequest as SdkWorkflowGraphUpdateRequest,
  WorkflowMode as SdkWorkflowMode,
  WorkflowUpdateRequest as SdkWorkflowUpdateRequest,
} from 'lemma-sdk';

export {
  DatastoreDataType,
  FunctionRunStatus,
  FunctionStatus,
  OrganizationInvitationStatus,
  OrganizationJoinPolicy,
  OrganizationRole,
  PodJoinPolicy,
  PodRole,
  ScheduleType,
};

export const AvailableModels = {} as Record<string, ConversationModel>;
export const ConnectorMode = {
  DYNAMIC: 'DYNAMIC',
  FIXED: 'FIXED',
} as const;
export const TableAccessMode = {
  READ: 'READ',
  WRITE: 'WRITE',
} as const;
export const ResourceType = {
  DATASTORE_TABLE: 'datastore_table',
  FOLDER: 'folder',
  DOCUMENT: 'document',
  APP: 'app',
  AGENT: 'agent',
  FUNCTION: 'function',
  WORKFLOW: 'workflow',
  SCHEDULE: 'schedule',
  CONNECTOR: 'connector',
  CONNECTOR_ACCOUNT: 'connector_account',
} as const;
export const ToolSet = AgentToolset;

type SdkAssistant = SdkAgent;
type SdkCreateAssistantInput = SdkCreateAgentInput;
type SdkUpdateAssistantInput = SdkUpdateAgentInput;
type SdkTask = SdkConversation & {
  agent_id?: string;
  user_id?: string;
  input_data?: TaskData;
  output_data?: TaskData;
  error?: string | null;
  status?: string | null;
};
type SdkTaskMessage = SdkConversationMessage & {
  task_id?: string;
  message_metadata?: TaskMessageMetadata;
  tool_calls?: Record<string, unknown>[];
};
export type ToolSet = AgentToolset;
export type ConnectorMode = (typeof ConnectorMode)[keyof typeof ConnectorMode];
export type TableAccessMode = (typeof TableAccessMode)[keyof typeof TableAccessMode];
export type ResourceType = (typeof ResourceType)[keyof typeof ResourceType];

export type {
  SdkAccount,
  SdkAgent,
  SdkAgentRuntimeConfig,
  SdkConnector,
  SdkAssistant,
  SdkAssistantSurface,
  SdkColumn,
  SdkConversation,
  SdkConversationMessage,
  SdkConversationModel,
  SdkCreateAgentInput,
  SdkCreateAssistantInput,
  SdkCreateFunctionRequest,
  SdkCreatePodInput,
  SdkDatastoreEventFlowStart,
  SdkEventFlowStart,
  SdkFileInfo,
  SdkFileResponse,
  SdkFlow,
  SdkFlowRun,
  SdkFlowStart,
  SdkFlowStartType,
  SdkFunction,
  SdkFunctionRun,
  SdkMessageResponse,
  SdkOrganizationSlugAvailabilityResponse,
  SdkOrganizationMessageResponse,
  SdkOrganization,
  SdkOrganizationInvitation,
  SdkOrganizationMember,
  SdkPod,
  SdkPodMember,
  SdkRecentUsageResponse,
  SdkSchedule,
  SdkCreateScheduleRequest,
  SdkScheduledFlowStart,
  SdkTable,
  SdkTask,
  SdkTaskMessage,
  SdkUpdateAgentInput,
  SdkUpdateAssistantInput,
  SdkUpdatePodInput,
  SdkUpdateScheduleRequest,
  SdkUploadedIcon,
  SdkUsageLimitsResponse,
  SdkUsageListResponse,
  SdkUsageRecordResponse,
  SdkUsageStatsResponse,
  SdkUsageSummaryResponse,
  SdkUser,
  SdkWorkflowCreateRequest,
  SdkWorkflowEdge,
  SdkWorkflowGraphUpdateRequest,
  SdkWorkflowMode,
  SdkWorkflowUpdateRequest,
};

export type Organization = SdkOrganization;

export type OrganizationSlugAvailability = SdkOrganizationSlugAvailabilityResponse;

export type UserProfile = SdkUser & {
  full_name?: string;
  avatar_url?: string;
};

export type OrganizationMember = Omit<SdkOrganizationMember, 'user'> & {
  user?: UserProfile | null;
};

export type OrganizationInvitation = SdkOrganizationInvitation;

export type ApiMessageResponse = SdkOrganizationMessageResponse

export type Pod = SdkPod;

export type UsageSummary = SdkUsageSummaryResponse;
export type UsageStats = SdkUsageStatsResponse;
export type UsageRecord = SdkUsageRecordResponse;
export type UsageLimits = SdkUsageLimitsResponse;
export type UsageList = SdkUsageListResponse;
export type RecentUsage = SdkRecentUsageResponse;

export interface PodSpec {
  name: string;
  description: string;
  markdown: string;
  resources?: {
    datastores?: string[];
    agents?: string[];
    workflows?: string[];
  };
}

export type ConnectorAccessConfig = {
  app_name: string;
  mode: ConnectorMode;
  account_id?: string | null;
};
export type AgentRuntimeConfig = SdkAgentRuntimeConfig;
export type ResourcePermissionGrant = {
  resource_type: ResourceType | string;
  resource_name: string;
  permission_ids?: string[];
};
export type TableAccessEntry = {
  table_name: string;
  mode: TableAccessMode;
};

type AgentRuntime = NonNullable<SdkAgent['agent_runtime']>;

export type Agent = Omit<SdkAgent, 'agent_runtime' | 'input_schema' | 'output_schema' | 'toolsets' | 'permissions'> & {
  agent_runtime?: SdkAgent['agent_runtime'];
  harness_kind?: string;
  input_schema: Record<string, unknown>;
  model_name?: AgentRuntime['model_name'];
  output_schema: Record<string, unknown>;
  tool_sets: ToolSet[];
  toolsets?: ToolSet[];
  permissions?: SdkAgentPermissions;
  accessible_connectors: ConnectorAccessConfig[];
  accessible_folders: string[];
  accessible_tables: TableAccessEntry[];
  agent_names?: string[];
  function_names?: string[];
};

export enum MessageRole {
  USER = 'user',
  AI = 'assistant',
  TOOL = 'tool',
}

export interface TaskMessageMetadata {
  tool_name?: string;
  message_type?: 'tool_call' | 'tool_return';
  tool_call_id?: string;
  is_final_answer?: boolean;
  isFinalAnswer?: boolean;
  structured_output?: unknown;
  structuredOutput?: unknown;
  args?: Record<string, unknown>;
  result?: {
    success?: boolean;
    message?: string;
    error?: string | null;
    [key: string]: unknown;
  };
}

type TaskData = Record<string, unknown> | string | null;

export type Task = Omit<SdkTask, 'created_at' | 'input_data' | 'output_data' | 'status' | 'updated_at'> & {
  created_at: string;
  input_data: TaskData;
  output_data: TaskData;
  error?: string | null;
  status: NonNullable<SdkTask['status']> | string;
  updated_at: string;
};

export type TaskMessage = Omit<SdkTaskMessage, 'role' | 'metadata'> & {
  task_id?: string;
  role: SdkTaskMessage['role'] | MessageRole;
  metadata?: SdkTaskMessage['metadata'] | TaskMessageMetadata | null;
  message_metadata?: TaskMessageMetadata;
  tool_calls?: Record<string, unknown>[];
};

export type TaskFileInfo = SdkFileInfo;

type FunctionJsonObject = Record<string, unknown>;

export type Function = Omit<SdkFunction, 'config' | 'config_schema' | 'input_schema' | 'output_schema' | 'permissions' | 'accessible_connectors' | 'accessible_folders' | 'accessible_tables'> & {
  config?: FunctionJsonObject | null;
  config_schema?: FunctionJsonObject | null;
  input_schema: FunctionJsonObject;
  output_schema: FunctionJsonObject;
  permissions?: SdkFunctionPermissions;
  accessible_connectors: ConnectorAccessConfig[];
  accessible_folders: string[];
  accessible_tables: TableAccessEntry[];
};

export type FunctionRun = SdkFunctionRun;

export type WorkflowStartType = `${SdkFlowStartType}`;
export type FlowStartType = WorkflowStartType;

export type ScheduledFlowStart = Partial<SdkScheduledFlowStart['config']> & {
  cron_expression?: string;
  timezone?: string;
};

export type EventFlowStart = Partial<SdkEventFlowStart['config']> & {
  event_type?: string;
};

export type DatastoreEventFlowStart = Omit<Partial<SdkDatastoreEventFlowStart['config']>, 'operations'> & {
  name?: string;
  table_name?: string;
  operations: Array<'INSERT' | 'UPDATE' | 'DELETE' | string>;
};

export type WorkflowStart = Omit<SdkFlowStart, 'type' | 'config'> & {
  type: WorkflowStartType;
  config?: ScheduledFlowStart | EventFlowStart | DatastoreEventFlowStart | null;
};
export type FlowStart = WorkflowStart;

export enum NodeType {
  START = 'START',
  AGENT = 'AGENT',
  FUNCTION = 'FUNCTION',
  FORM = 'FORM',
  DECISION = 'DECISION',
  LOOP = 'LOOP',
  WAIT_UNTIL = 'WAIT_UNTIL',
  END = 'END',
}

export interface WorkflowNode {
  id: string;
  type: NodeType;
  config: Record<string, unknown>;
  label?: string;
}
export type FlowNode = WorkflowNode;

export type WorkflowEdge = SdkWorkflowEdge & {
  edge_type?: string;
  condition?: string;
};
export type FlowEdge = WorkflowEdge;

export interface WorkflowDefinition {
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  viewport?: Record<string, unknown>;
}
export type FlowDefinition = WorkflowDefinition;

export type WorkflowInstallMode = `${SdkWorkflowMode}`;

export type Workflow = Omit<SdkFlow, 'nodes' | 'edges' | 'start' | 'mode'> & {
  nodes?: WorkflowNode[];
  edges?: WorkflowEdge[];
  viewport?: Record<string, unknown>;
  start?: WorkflowStart | null;
  mode?: WorkflowInstallMode | null;
  // Present on list (summary) responses; absent on the full detail response
  // (derive from `nodes` there).
  node_count?: number;
  node_types?: string[];
};
export type Flow = Workflow;

export type WorkflowCreateRequest = Omit<SdkWorkflowCreateRequest, 'start' | 'mode'> & {
  start?: WorkflowStart | null;
  mode?: WorkflowInstallMode;
};
export type FlowCreateRequest = WorkflowCreateRequest;

export type WorkflowUpdateInput = Omit<SdkWorkflowUpdateRequest, 'start' | 'mode'> & {
  start?: WorkflowStart | null;
  mode?: WorkflowInstallMode | null;
};
export type FlowUpdateInput = WorkflowUpdateInput;

export type WorkflowGraphUpdateInput = Omit<SdkWorkflowGraphUpdateRequest, 'nodes' | 'edges' | 'start'> & {
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  start?: WorkflowStart | null;
};
export type FlowGraphUpdateInput = WorkflowGraphUpdateInput;

export type WorkflowRun = Omit<SdkFlowRun, 'id' | 'status' | 'current_node_id'> & {
  id: string;
  status?: SdkFlowRun['status'];
  current_node_id?: string | null;
  trigger_type?: string;
  created_at: string;
  updated_at: string;
};
export type WorkflowRunActiveWait = NonNullable<SdkFlowRun['active_wait']>;
export type FlowRun = WorkflowRun;

export type Schedule = SdkSchedule;
export type CreateScheduleRequest = SdkCreateScheduleRequest;
export type UpdateScheduleRequest = SdkUpdateScheduleRequest;

export type Datastore = {
  id: string;
  name: string;
  description?: string;
  user_id?: string;
  created_at: string;
  updated_at: string;
};

export type Column = Omit<
  Partial<SdkColumn>,
  'name' | 'type' | 'description' | 'default' | 'foreign_key' | 'max_length' | 'type_params' | 'options' | 'expression'
> & {
  name: string;
  type: DatastoreDataType;
  description?: string;
  default?: unknown;
  foreign_key?: { references: string };
  max_length?: number;
  type_params?: Record<string, unknown>;
  options?: string[];
  auto?: boolean;
  computed?: boolean;
  expression?: string;
};

export type Table = Omit<Partial<SdkTable>, 'name' | 'primary_key_column' | 'columns'> & {
  name: string;
  primary_key_column: string;
  columns: Column[];
};

export type DatastoreFile = SdkFileResponse;

export type Connector = SdkConnector & {
  name?: string;
};

export type Account = Omit<SdkAccount, 'connector'> & {
  connector?: Connector;
  /** Auth provider ("LEMMA" or "COMPOSIO") backing this account's auth config. */
  provider?: string | null;
};

export type AuthConfig = SdkAuthConfig;

export type AssistantSurface = SdkAssistantSurface & {
  agent_id?: string | null;
  agent_name?: string | null;
  assistant_id?: string | null;
  assistant_name?: string | null;
};

export type Assistant = Agent;

export const AVAILABLE_CONVERSATION_MODELS: ConversationModel[] = [];

export type AvailableModels = SdkConversationModel;

export type ConversationModel = SdkConversationModel;

export type Conversation = Omit<SdkConversation, 'model' | 'status'> & {
  model?: ConversationModel | null;
  status?: 'waiting' | 'running' | string | null;
};

export type Message = Omit<SdkConversationMessage, 'role' | 'metadata'> & {
  conversation_id: string;
  role: MessageRole;
  metadata?: TaskMessageMetadata | null;
  message_metadata?: TaskMessageMetadata;
  tool_calls?: Record<string, unknown>[];
};

export interface PaginatedResponse<T> {
  items: T[];
  limit: number;
  next_page_cursor?: string | null;
  total?: number;
}

export type CreatePodData = SdkCreatePodInput;

export type UpdatePodData = SdkUpdatePodInput;

export type CreateAgentData = Omit<SdkCreateAgentInput, 'toolsets'> & {
  tool_sets?: ToolSet[];
  toolsets?: ToolSet[];
  accessible_connectors?: ConnectorAccessConfig[];
  accessible_folders?: string[];
  accessible_tables?: TableAccessEntry[];
  accessible_functions?: string[];
  accessible_agents?: string[];
};

export type UpdateAgentData = Omit<SdkUpdateAgentInput, 'toolsets'> & {
  tool_sets?: ToolSet[] | null;
  toolsets?: ToolSet[] | null;
  accessible_connectors?: ConnectorAccessConfig[] | null;
  accessible_folders?: string[] | null;
  accessible_tables?: TableAccessEntry[] | null;
  accessible_functions?: string[] | null;
  accessible_agents?: string[] | null;
};

export type CreateFunctionData = Omit<SdkCreateFunctionRequest, 'config' | 'config_schema' | 'input_schema' | 'output_schema'> & {
  config?: FunctionJsonObject | null;
  config_schema?: FunctionJsonObject | null;
  input_schema?: FunctionJsonObject;
  output_schema?: FunctionJsonObject;
  accessible_connectors?: ConnectorAccessConfig[];
  accessible_folders?: string[];
  accessible_tables?: TableAccessEntry[];
};

export type UpdateFunctionData = Omit<SdkUpdateFunctionRequest, 'config'> & {
  config?: FunctionJsonObject | null;
  config_schema?: FunctionJsonObject | null;
  input_schema?: FunctionJsonObject;
  output_schema?: FunctionJsonObject;
  accessible_connectors?: ConnectorAccessConfig[] | null;
  accessible_folders?: string[] | null;
  accessible_tables?: TableAccessEntry[] | null;
};

export type CreateAssistantData = CreateAgentData;

export type UpdateAssistantData = UpdateAgentData;
