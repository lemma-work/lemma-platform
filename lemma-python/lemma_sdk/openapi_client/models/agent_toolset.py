from enum import Enum


class AgentToolset(str, Enum):
    CONNECTORS = "CONNECTORS"
    MESSAGING = "MESSAGING"
    POD = "POD"
    SKILLS = "SKILLS"
    SNOOZE = "SNOOZE"
    SPEECH = "SPEECH"
    SUBAGENTS = "SUBAGENTS"
    TODO = "TODO"
    USER_INTERACTION = "USER_INTERACTION"
    VIEW_IMAGE = "VIEW_IMAGE"
    WEB_SEARCH = "WEB_SEARCH"
    WORKSPACE_CLI = "WORKSPACE_CLI"

    def __str__(self) -> str:
        return str(self.value)
