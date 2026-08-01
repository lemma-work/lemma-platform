from enum import Enum


class ConnectorKind(str, Enum):
    COMPOSIO = "composio"
    HTTP = "http"
    MCP = "mcp"
    PACKAGE = "package"
    SQL = "sql"

    def __str__(self) -> str:
        return str(self.value)
