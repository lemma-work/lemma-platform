from enum import Enum


class ResourceType(str, Enum):
    AGENT = "agent"
    APP = "app"
    CONNECTOR = "connector"
    CONNECTOR_ACCOUNT = "connector_account"
    CONNECTOR_AUTH_CONFIG = "connector_auth_config"
    CONVERSATION = "conversation"
    DATASTORE_RECORD = "datastore_record"
    DATASTORE_TABLE = "datastore_table"
    DOCUMENT = "document"
    FOLDER = "folder"
    FUNCTION = "function"
    ORGANIZATION = "organization"
    POD = "pod"
    POD_MEMBER = "pod_member"
    ROLE = "role"
    SCHEDULE = "schedule"
    WEB_LOGIN = "web_login"
    WORKFLOW = "workflow"

    def __str__(self) -> str:
        return str(self.value)
