"""Settings only `mod:mcp_access` reads."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.settings_env import dotenv_path


class McpAccessSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=dotenv_path(),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    mcp_access_enabled: bool = Field(
        default=True,
        description=(
            "Serve pods to outside MCP clients at /mcp/{pod_id}, with the OAuth "
            "authorization server under /oauth. Off answers 404 on both."
        ),
    )
    mcp_access_requests_per_minute: int = Field(
        default=300,
        ge=1,
        description=(
            "MCP requests one connected client may make to one pod per minute. "
            "Per grant rather than per person, so one runaway client does not "
            "lock the person out of the others."
        ),
    )
    mcp_access_registrations_per_hour: int = Field(
        default=30,
        ge=1,
        description=(
            "Dynamic client registrations per source IP per hour. Registration "
            "is unauthenticated by design (RFC 7591), so this is its only limit."
        ),
    )
    mcp_access_token_requests_per_minute: int = Field(
        default=60,
        ge=1,
        description="Token endpoint requests per source IP per minute.",
    )


mcp_access_settings = McpAccessSettings()
