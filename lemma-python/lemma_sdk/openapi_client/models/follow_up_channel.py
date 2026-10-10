from enum import Enum


class FollowUpChannel(str, Enum):
    EMAIL = "email"
    LATEST = "latest"

    def __str__(self) -> str:
        return str(self.value)
