from typing import Any

from pydantic import BaseModel, ConfigDict


class BaseSchema(BaseModel):
    """Base schema with common configuration."""

    model_config = ConfigDict(from_attributes=True)


class ErrorResponse(BaseModel):
    """Unified error envelope returned by every error response.

    All error responses (domain errors, HTTP exceptions, request validation
    failures, and unexpected errors) share this shape. See
    ``app.core.api.exception_handlers``.
    """

    message: str
    code: str
    request_id: str | None = None
    details: Any | None = None
