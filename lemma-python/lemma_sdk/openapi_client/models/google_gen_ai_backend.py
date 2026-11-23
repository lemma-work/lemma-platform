from enum import Enum


class GoogleGenAIBackend(str, Enum):
    GEMINI_API = "GEMINI_API"
    VERTEX_AI = "VERTEX_AI"

    def __str__(self) -> str:
        return str(self.value)
