from enum import Enum


class ChannelMessageResponseVisibility(str, Enum):
    ASKER_ONLY = "asker_only"
    CHANNEL = "channel"

    def __str__(self) -> str:
        return str(self.value)
