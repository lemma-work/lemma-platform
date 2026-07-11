"""Compatibility normalization for enum statuses persisted before uppercase values."""

from enum import Enum

from app.modules.agent.domain.value_objects import AgentRunStatus, ConversationStatus


def enum_status_values_for_db(statuses: object, enum_type: type[Enum]) -> list[str]:
    normalized = [statuses] if isinstance(statuses, (enum_type, str)) else list(statuses)
    values: list[str] = []
    for status in normalized:
        member = enum_type(status)
        values.extend([member.value, member.value.lower()])
    return list(dict.fromkeys(values))


def run_status_values_for_db(statuses: object) -> list[str]:
    return enum_status_values_for_db(statuses, AgentRunStatus)


def conversation_status_values_for_db(statuses: object) -> list[str]:
    return enum_status_values_for_db(statuses, ConversationStatus)
