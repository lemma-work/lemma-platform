from enum import Enum


class ResetScopeResultScope(str, Enum):
    BROWSER_SESSION = "browser-session"
    CACHES = "caches"
    DOCKER = "docker"
    HOME = "home"
    PROCESSES = "processes"
    RUNS = "runs"

    def __str__(self) -> str:
        return str(self.value)
