from enum import Enum


class ChannelKind(str, Enum):
    CONTACT = "CONTACT"
    DIRECT = "DIRECT"
    GROUP = "GROUP"

    def __str__(self) -> str:
        return str(self.value)
