from __future__ import annotations

import enum
from typing import Any, Dict, Optional
from uuid import UUID

from pydantic import Field

from app.core.domain.entity import Entity


class ConnectRequestStatus(str, enum.Enum):
    """Connect request status choices."""

    PENDING = "PENDING"
    #: Claimed by a callback that is mid-exchange. A separate state, and not
    #: merely PENDING with a flag, because it is what makes spending a `state`
    #: atomic: the claim is an UPDATE with `status = 'PENDING'` in its WHERE,
    #: so of two concurrent callbacks exactly one changes a row.
    EXCHANGING = "EXCHANGING"
    SUCCESS = "SUCCESS"
    ERROR = "ERROR"


class ConnectRequestEntity(Entity):
    """Entity for request to connect a user to a connector."""

    user_id: UUID = Field(..., description="ID of the user initiating the request")
    organization_id: UUID = Field(..., description="ID of the organization scope")
    auth_config_id: UUID = Field(..., description="ID of the org auth config")
    connector_id: str = Field(..., description="ID of the connector to connect to")

    authorization_url: Optional[str] = Field(
        None, description="URL for OAuth authorization"
    )
    status: ConnectRequestStatus = Field(
        ConnectRequestStatus.PENDING, description="Status of the connection request"
    )

    attributes: Optional[Dict[str, Any]] = Field(
        None, description="Additional attributes like state"
    )
