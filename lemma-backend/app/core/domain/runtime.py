"""Shared runtime selector used by pod, agent, workflow, and bundle contracts."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_serializer

from app.core.domain.generation import GenerationSettings


class AgentRuntimeConfig(BaseModel):
    """Select an agent runtime profile, an optional catalog model, and how it generates."""

    profile_id: str = Field(min_length=1)
    model_name: str | None = Field(default=None, min_length=1)
    # Copied agent -> conversation -> run, so a run's stored runtime is a record
    # of the settings it was started with. Layered over the model's catalog
    # defaults and the profile's settings when the run resolves its model.
    settings: GenerationSettings | None = None
    # A coding agent's own options -- effort, mode -- by option id, for an
    # Agent Host profile. Layered per key over the profile's saved selections,
    # narrowest wins: profile, then pod default, then teammate, then
    # conversation. Checked against what the harness offers for the run's
    # model when saved, and a value it no longer offers is dropped at dispatch.
    config_selections: dict[str, str] | None = None

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

    @field_validator("settings")
    @classmethod
    def drop_empty_settings(
        cls, value: GenerationSettings | None
    ) -> GenerationSettings | None:
        return None if value is None or value.is_empty() else value

    @field_validator("config_selections")
    @classmethod
    def normalize_config_selections(
        cls, value: dict[str, str] | None
    ) -> dict[str, str] | None:
        if not value:
            return None
        normalized: dict[str, str] = {}
        for key, selection in value.items():
            option_id = key.strip()
            if not option_id:
                raise ValueError("config_selections keys cannot be empty")
            normalized[option_id] = selection
        return normalized

    @model_serializer(mode="wrap")
    def serialize_without_unset_fields(self, handler):
        data = handler(self)
        for key in ("model_name", "settings", "config_selections"):
            if data.get(key) is None:
                data.pop(key, None)
        return data

    def with_overrides(
        self,
        *,
        model_name: str | None = None,
        settings: GenerationSettings | None = None,
        config_selections: dict[str, str] | None = None,
    ) -> AgentRuntimeConfig:
        """This runtime with a caller's narrower choice layered on top.

        A workflow step uses it: the step may pin another model of the same
        profile and change how it generates, and whatever it leaves unset is
        still the agent's. Selections layer per option, like settings.
        """
        merged = (self.settings or GenerationSettings()).overlaid_with(settings)
        return AgentRuntimeConfig(
            profile_id=self.profile_id,
            model_name=model_name or self.model_name,
            settings=merged,
            config_selections={
                **(self.config_selections or {}),
                **(config_selections or {}),
            },
        )

    def layered_over(
        self, broader: list[AgentRuntimeConfig | None]
    ) -> AgentRuntimeConfig:
        """This runtime, its selections laid over those of ``broader`` ones.

        ``broader`` runs broadest first -- a pod's default, then a teammate's
        runtime -- and only those on this runtime's profile count: an option id
        means something only to the harness it was chosen for. Narrowest wins
        per key, so a conversation that picks only an effort keeps the
        teammate's mode.
        """
        layered: dict[str, str] = {}
        for runtime in [*broader, self]:
            if runtime is None or runtime.profile_id != self.profile_id:
                continue
            layered.update(runtime.config_selections or {})
        if layered == (self.config_selections or {}):
            return self
        return self.model_copy(update={"config_selections": layered or None})

    def inheriting_settings_from(
        self, other: AgentRuntimeConfig | None
    ) -> AgentRuntimeConfig:
        """This runtime, falling back to ``other``'s settings when it has none.

        Picking another model for one conversation is a choice of model, not a
        decision to forget how the agent was told to generate.
        """
        if self.settings is not None or other is None or other.settings is None:
            return self
        return self.model_copy(update={"settings": other.settings})
