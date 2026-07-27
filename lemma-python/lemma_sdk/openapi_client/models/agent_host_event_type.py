from enum import Enum


class AgentHostEventType(str, Enum):
    AGENT_MESSAGE_CHUNK = "agent_message_chunk"
    AGENT_MESSAGE_UPSERT = "agent_message_upsert"
    AGENT_THOUGHT_CHUNK = "agent_thought_chunk"
    AGENT_THOUGHT_UPSERT = "agent_thought_upsert"
    CONFIG_UPDATE = "config_update"
    INPUT_REQUEST = "input_request"
    PERMISSION_REQUEST = "permission_request"
    PLAN_UPSERT = "plan_upsert"
    RUN_STATE = "run_state"
    TERMINAL = "terminal"
    TOOL_CALL_UPDATE = "tool_call_update"
    TOOL_CALL_UPSERT = "tool_call_upsert"
    USAGE_UPDATE = "usage_update"
    USER_MESSAGE = "user_message"
    WARNING = "warning"

    def __str__(self) -> str:
        return str(self.value)
