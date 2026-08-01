from enum import Enum


class SendAudience(str, Enum):
    NOBODY = "NOBODY"
    POD_MEMBERS = "POD_MEMBERS"
    SELF = "SELF"

    def __str__(self) -> str:
        return str(self.value)
