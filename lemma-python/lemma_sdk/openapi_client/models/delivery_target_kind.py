from enum import Enum


class DeliveryTargetKind(str, Enum):
    MEMBER = "member"
    SOURCE = "source"

    def __str__(self) -> str:
        return str(self.value)
