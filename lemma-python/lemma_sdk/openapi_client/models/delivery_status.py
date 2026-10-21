from enum import Enum


class DeliveryStatus(str, Enum):
    DEAD = "DEAD"
    FAILED = "FAILED"
    PENDING = "PENDING"
    SENT = "SENT"
    SKIPPED = "SKIPPED"

    def __str__(self) -> str:
        return str(self.value)
