from enum import Enum


class OutputChunkModelChannel(str, Enum):
    PTY = "pty"
    STDERR = "stderr"
    STDOUT = "stdout"

    def __str__(self) -> str:
        return str(self.value)
