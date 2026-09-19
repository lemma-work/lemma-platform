from enum import Enum


class AgentToolset(str, Enum):
    BROWSER = "BROWSER"
    CONNECTORS = "CONNECTORS"
    MEMORY = "MEMORY"
    MESSAGING = "MESSAGING"
    POD = "POD"
    SKILLS = "SKILLS"
    SPEECH = "SPEECH"
    SUBAGENTS = "SUBAGENTS"
    TODO = "TODO"
    USER_INTERACTION = "USER_INTERACTION"
    VIEW_IMAGE = "VIEW_IMAGE"
    WAIT = "WAIT"
    WEB_SEARCH = "WEB_SEARCH"
    WORKSPACE_CLI = "WORKSPACE_CLI"

    def __str__(self) -> str:
        return str(self.value)
