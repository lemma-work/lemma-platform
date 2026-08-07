from enum import Enum


class NotificationStatus(str, Enum):
    ACKNOWLEDGED = "ACKNOWLEDGED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    OPEN = "OPEN"
    RESPONDED = "RESPONDED"

    def __str__(self) -> str:
        return str(self.value)
