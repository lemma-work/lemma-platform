from enum import Enum


class CallResponseEnginesItem(str, Enum):
    CASCADE = "cascade"
    GEMINI_LIVE = "gemini_live"
    OPENAI_REALTIME = "openai_realtime"

    def __str__(self) -> str:
        return str(self.value)
