from enum import Enum


class WidgetKind(str, Enum):
    CHAT = "chat"
    FORM = "form"

    def __str__(self) -> str:
        return str(self.value)
