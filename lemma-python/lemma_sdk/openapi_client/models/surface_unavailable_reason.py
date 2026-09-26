from enum import Enum


class SurfaceUnavailableReason(str, Enum):
    NEEDS_EMAIL_DOMAIN = "NEEDS_EMAIL_DOMAIN"
    NEEDS_PUBLIC_LINK = "NEEDS_PUBLIC_LINK"

    def __str__(self) -> str:
        return str(self.value)
