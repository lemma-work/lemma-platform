"""Request and response models for installing several plugins at once
(``/pods/{pod_id}/bundle/installs``).

Apart from ``schemas.py`` beside it, which is a single bundle's import, export
and publish API.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.pod_bundle.api.schemas import ExportProgressResponse
from app.modules.pod_bundle.application.install_use_cases import (
    Install,
    PluginSource,
)
from app.modules.pod_bundle.domain.plugin_install import (
    MAX_BUNDLES_PER_INSTALL,
    PluginInstallState,
    bundle_name,
    installed_app,
    outcome_of,
    settled,
)
from app.modules.pod_bundle.domain.state import (
    BUNDLE_PATH_MAX_LENGTH,
    BUNDLE_PATH_PATTERN,
    ImportState,
)


class PluginSourceRequest(BaseModel):
    """One plugin: a GitHub repository, and the folder in it its bundle is in."""

    model_config = ConfigDict(extra="forbid")

    repo_url: str | None = Field(
        default=None,
        description="The repository's URL (alternative to owner+repo).",
    )
    owner: str | None = Field(default=None, description="Repository owner.")
    repo: str | None = Field(default=None, description="Repository name.")
    path: str | None = Field(
        default=None,
        max_length=BUNDLE_PATH_MAX_LENGTH,
        pattern=BUNDLE_PATH_PATTERN,
        description=(
            "The folder that holds the bundle's `pod.json`, e.g. 'install/mail'. "
            "Omit for a repository that is one bundle."
        ),
    )
    ref: str | None = Field(default=None, description="Branch, tag or sha.")

    @model_validator(mode="after")
    def _names_a_repository(self) -> "PluginSourceRequest":
        if not self.repo_url and not (self.owner and self.repo):
            raise ValueError("Name the repository: repo_url, or owner and repo.")
        return self

    def source(self) -> PluginSource:
        return PluginSource(
            owner=self.owner,
            repo=self.repo,
            repo_url=self.repo_url,
            path=self.path,
            ref=self.ref,
        )

    def where(self) -> tuple[str, str | None, str | None]:
        repository = self.repo_url or f"https://github.com/{self.owner}/{self.repo}"
        return repository.rstrip("/").removesuffix(".git").lower(), self.path, self.ref


class PluginInstallRequest(BaseModel):
    """Body for installing several plugins into one Lemma."""

    model_config = ConfigDict(extra="forbid")

    plugins: list[PluginSourceRequest] = Field(
        ...,
        min_length=1,
        max_length=MAX_BUNDLES_PER_INSTALL,
        description=(
            "The plugins to install, in the order to install them. Each counts "
            "as one import against the daily import limit."
        ),
    )

    @model_validator(mode="after")
    def _each_once(self) -> "PluginInstallRequest":
        places = [plugin.where() for plugin in self.plugins]
        if len(set(places)) != len(places):
            raise ValueError("A plugin is named twice; name each one once.")
        return self


class PluginInstallStepResponse(BaseModel):
    """The step a bundle is on."""

    index: int
    kind: str
    name: str


class PluginInstallItemResponse(BaseModel):
    """Where one plugin of an install is."""

    position: int = Field(..., description="Its place in the request, from 0.")
    import_id: UUID = Field(
        ...,
        description=(
            "Its import: GET /pods/{pod_id}/bundle/imports/{import_id} has its "
            "whole plan and every step."
        ),
    )
    repo_url: str
    path: str | None = None
    ref: str | None = None
    name: str = Field(
        ..., description="The bundle's own name once planned, else its folder's."
    )
    state: PluginInstallState = Field(
        ...,
        description=(
            "WAITING behind the plugins before it; INSTALLING; INSTALLED; "
            "NEEDS_ANSWER -- stopped before anything was applied, because its "
            "install page would ask the question in `reason`; FAILED -- "
            "stopped, and POST …/retry continues it from the failed step; "
            "CANCELLED by somebody."
        ),
    )
    progress: ExportProgressResponse = Field(
        default_factory=ExportProgressResponse,
        description="Plan steps done (or skipped) of all its steps; 0/0 before its plan.",
    )
    current_step: PluginInstallStepResponse | None = Field(
        default=None, description="The step running now, if one is."
    )
    reason: str | None = Field(
        default=None,
        description=(
            "Why it stopped: the question its page asks (NEEDS_ANSWER), or what "
            "failed (FAILED)."
        ),
    )
    error_code: str | None = None
    connect_url: str | None = Field(
        default=None,
        description="Where to connect the account a failure names, if it names one.",
    )
    app: str | None = Field(
        default=None,
        description="The app it put in the Lemma, once INSTALLED, if it brings one.",
    )
    warnings: list[str] = Field(default_factory=list)

    @classmethod
    def from_state(cls, state: ImportState) -> "PluginInstallItemResponse":
        assert state.install is not None, "an install's import"
        outcome = outcome_of(state)
        step = next(
            (
                s
                for s in (state.plan.steps if state.plan else ())
                if s.index == state.current_step
            ),
            None,
        )
        reason = (
            state.install.asks
            if outcome is PluginInstallState.NEEDS_ANSWER
            else state.error
            if outcome is PluginInstallState.FAILED
            else None
        )
        return cls(
            position=state.install.position,
            import_id=state.import_id,
            repo_url=state.source.repo_url or "",
            path=state.source.path,
            ref=state.source.ref,
            name=bundle_name(state),
            state=outcome,
            progress=ExportProgressResponse.from_domain(state.progress),
            current_step=(
                PluginInstallStepResponse(
                    index=step.index, kind=step.kind.value, name=step.name
                )
                if step is not None
                else None
            ),
            reason=reason,
            error_code=state.error_code
            if outcome is PluginInstallState.FAILED
            else None,
            connect_url=state.connect_url
            if outcome is PluginInstallState.FAILED
            else None,
            app=installed_app(state),
            warnings=state.warnings,
        )


class PluginInstallResponse(BaseModel):
    """A plugin install: every plugin it named, in order."""

    install_id: UUID
    pod_id: UUID
    finished: bool = Field(
        ...,
        description=(
            "Every plugin has stopped moving: INSTALLED, NEEDS_ANSWER, FAILED or "
            "CANCELLED. The Lemma tells the person who started it in their chat "
            "with it when this turns true."
        ),
    )
    plugins: list[PluginInstallItemResponse]

    @classmethod
    def from_install(cls, install: Install) -> "PluginInstallResponse":
        return cls(
            install_id=install.install_id,
            pod_id=install.pod_id,
            finished=settled(install.imports),
            plugins=[PluginInstallItemResponse.from_state(s) for s in install.imports],
        )


__all__ = [
    "PluginInstallItemResponse",
    "PluginInstallRequest",
    "PluginInstallResponse",
    "PluginInstallStepResponse",
    "PluginSourceRequest",
]
