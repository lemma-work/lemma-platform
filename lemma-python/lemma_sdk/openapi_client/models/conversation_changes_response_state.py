from enum import Enum


class ConversationChangesResponseState(str, Enum):
    ELSEWHERE = "elsewhere"
    MACHINE_UNAVAILABLE = "machine_unavailable"
    NOT_CHECKED_OUT = "not_checked_out"
    NO_REPOSITORY = "no_repository"
    READY = "ready"

    def __str__(self) -> str:
        return str(self.value)
