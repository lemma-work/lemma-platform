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
        default=300,
        ge=1,
        description=(
            "Dynamic client registrations per source IP per hour. Registration "
            "is unauthenticated by design (RFC 7591), so this is its only limit. "
            "Sized for a hosted client registering from a few shared addresses."
        ),
    )
    mcp_access_token_requests_per_minute: int = Field(
        default=600,
        ge=1,
        description=(
            "Token and revocation requests per client per source IP per minute. "
            "Claude and ChatGPT refresh for all of their users from a small set "
            "of addresses, so this is per client as well as per address, and "
            "sized for that."
        ),
    )
    mcp_access_token_requests_per_address_per_minute: int = Field(
        default=6000,
        ge=1,
        description=(
            "Token and revocation requests per source IP per minute, whatever "
            "client they name. The per-client limit alone does not bound one "
            "address, since the caller chooses the client_id. Ten times the "
            "per-client limit, so a hosted client's shared addresses fit."
        ),
    )
    mcp_access_authorize_requests_per_minute: int = Field(
        default=60,
        ge=1,
        description=(
            "Authorization requests per source IP per minute. Each one holds a "
            "pending request in Redis until it is answered or expires, and "
            "these come from people's browsers, not from a client's servers."
        ),
    )


mcp_access_settings = McpAccessSettings()
