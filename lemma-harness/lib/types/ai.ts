// AI Assistant Types

import type { Pod, Agent, Function, Flow, Datastore, Account } from './index';
import type { AppPageRef } from './app';

// Table schema information for AI context
export interface TableColumnInfo {
    name: string;
    type: string;
    required?: boolean;
    unique?: boolean;
    description?: string;
}

export interface TableInfo {
    name: string;
    columns: TableColumnInfo[];
    record_count?: number;
}

// Enriched datastore with table schemas
export interface EnrichedDatastore extends Datastore {
    tables: TableInfo[];
    isLinked?: boolean;
}

// Context passed to AI for understanding current pod state
export interface PodContext {
    pod: Pod;
    agents: Agent[];
    functions: Function[];
    flows: Flow[];
    datastores: EnrichedDatastore[];
    appPages: AppPageRef[];
    connectedAccounts: Account[];
}

// Global assistant context (cross-pod)
export interface AssistantContext {
    currentOrganizationId?: string;
    organizations: Array<{ id: string; name: string }>;
    pods: Array<{ id: string; name: string; description?: string; organization_id: string }>;
}

// Represents an action the AI is taking or has taken
export interface AIAction {
    id: string;
    type: 'tool_call' | 'message' | 'thinking';
    status: 'pending' | 'executing' | 'completed' | 'failed';
    toolName?: string;
    toolArgs?: Record<string, unknown>;
    result?: unknown;
    error?: string;
    timestamp: Date;
    // For linking to created resources
    resourceType?: 'pod' | 'agent' | 'function' | 'flow' | 'datastore' | 'app_page' | 'table' | 'record' | 'connector';
    resourceId?: string;
}

// Chat message in the assistant conversation
export interface ChatMessage {
    id: string;
    role: 'user' | 'assistant' | 'system';
    content: string;
    actions?: AIAction[]; // Tool calls associated with this message
    createdAt: Date;
}

// State of the assistant panel
export interface AssistantState {
    isOpen: boolean;
    messages: ChatMessage[];
    pendingActions: AIAction[];
    isStreaming: boolean;
    error?: string;
}

// Tool call from AI
export interface ToolCall {
    id: string;
    name: string;
    arguments: Record<string, unknown>;
    // arguments: Record<string, any>; // was type any
}

// Tool result to send back to AI
export interface ToolResult {
    toolCallId: string;
    result: unknown;
    // result: any; // was type any
    error?: string;
}

// Available tool names
export type AIToolName =
    | 'create_agent'
    | 'update_agent'
    | 'delete_agent'
    | 'create_task'
    | 'list_tasks'
    | 'get_task'
    | 'list_task_messages'
    | 'stop_task'
    | 'create_function'
    | 'update_function'
    | 'delete_function'
    | 'create_flow'
    | 'update_flow'
    | 'update_flow_graph'
    | 'delete_flow'
    | 'create_datastore'
    | 'update_datastore'
    | 'delete_datastore'
    | 'create_table'
    | 'list_tables'
    | 'delete_table'
    | 'create_record'
    | 'list_records'
    | 'update_record'
    | 'delete_record'
    | 'query_records'
    | 'save_react_app_page'
    | 'generate_react_app_page'
    | 'run_function'
    | 'run_flow'
    | 'install_flow'
    | 'uninstall_flow'
    | 'get_pod_info'
    | 'list_resources'
    | 'list_connectors'
    | 'list_connector_triggers'
    | 'get_connector_trigger'
    | 'list_connected_accounts'
    | 'connect_connector';

// Suggested prompt for context-aware suggestions
export interface SuggestedPrompt {
    label: string;
    prompt: string;
    icon?: string;
}
