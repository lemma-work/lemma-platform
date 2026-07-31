"""Shared runtime selector used by pod, agent, workflow, and bundle contracts."""

from pydantic import BaseModel, Field, field_validator, model_serializer


class AgentRuntimeConfig(BaseModel):
    """Select an agent runtime profile and optional catalog model."""

    profile_id: str = Field(min_length=1)
    model_name: str | None = Field(default=None, min_length=1)

    @field_validator("profile_id")
    @classmethod
    def normalize_profile_id(cls, value: str) -> str:
        profile_id = value.strip()
        if not profile_id:
            raise ValueError("profile_id cannot be empty")
        return profile_id

    @field_validator("model_name")
    @classmethod
    def normalize_model_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        model_name = value.strip()
        if not model_name:
            raise ValueError("model_name cannot be empty")
        return model_name

    @model_serializer(mode="wrap")
    def serialize_without_unset_model_name(self, handler):
        data = handler(self)
        if data.get("model_name") is None:
            data.pop("model_name", None)
        return data
