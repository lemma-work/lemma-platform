/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export enum AgentHostEventType {
    RUN_STATE = 'run_state',
    USER_MESSAGE = 'user_message',
    AGENT_MESSAGE_CHUNK = 'agent_message_chunk',
    AGENT_MESSAGE_UPSERT = 'agent_message_upsert',
    AGENT_THOUGHT_CHUNK = 'agent_thought_chunk',
    AGENT_THOUGHT_UPSERT = 'agent_thought_upsert',
    PLAN_UPSERT = 'plan_upsert',
    TOOL_CALL_UPSERT = 'tool_call_upsert',
    TOOL_CALL_UPDATE = 'tool_call_update',
    USAGE_UPDATE = 'usage_update',
    CONFIG_UPDATE = 'config_update',
    PERMISSION_REQUEST = 'permission_request',
    INPUT_REQUEST = 'input_request',
    WARNING = 'warning',
    TERMINAL = 'terminal',
}
