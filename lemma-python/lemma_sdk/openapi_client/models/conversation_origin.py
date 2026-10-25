from enum import Enum


class ConversationOrigin(str, Enum):
    ASK = "ask"
    CHANNEL = "channel"
    CHAT = "chat"
    NOTIFICATION = "notification"
    RESOURCE = "resource"
    SCHEDULE = "schedule"
    TASK = "task"
    WORKFLOW = "workflow"

    def __str__(self) -> str:
        return str(self.value)
