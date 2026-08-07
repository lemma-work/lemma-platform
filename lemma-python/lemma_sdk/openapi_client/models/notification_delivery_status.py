from enum import Enum


class NotificationDeliveryStatus(str, Enum):
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    PENDING = "PENDING"
    UNDELIVERABLE = "UNDELIVERABLE"

    def __str__(self) -> str:
        return str(self.value)
