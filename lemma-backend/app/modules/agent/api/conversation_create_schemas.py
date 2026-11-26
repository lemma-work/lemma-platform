"""What opening a conversation takes: who answers, on what, and where it works.

Its own module because the conversation schemas page is held to the size cap,
and this request is the one that grows with each place a conversation can be
opened in.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.modules.agent.domain.project_repo import (
    ProjectRepo,
    is_checkout_ref,
    parse_repository_name,
)
from app.modules.agent.domain.value_objects import (
    AgentRuntimeConfig,
    ConversationType,
    JsonObject,
)


class CreateConversationRequest(BaseModel):
    agent_name: str | None = None
    title: str | None = None
    instructions: str | None = None
    agent_runtime: AgentRuntimeConfig | None = None
    parent_id: UUID | None = None
    type: ConversationType = ConversationType.CHAT
    metadata: JsonObject | None = None
    repository: str | None = Field(
        default=None,
        max_length=140,
        description=(
            "A GitHub repository to work in, as 'owner/name': one the "
            "organization's GitHub installation covers for you, as listed by "
            "the conversation repositories endpoint. The conversation's "
            "workspace_cwd is then a git worktree of it on a branch of its own "
            "(lemma/<slug>), made before the agent's first command from one "
            "clone of the repository, which is fetched first. Other "
            "conversations in the repository get worktrees of their own."
        ),
    )
    ref: str | None = Field(
        default=None,
        max_length=255,
        description=(
            "Where in `repository` the worktree starts: a branch, a tag, a "
            "commit, or a pull request's head as 'pull/<number>/head'. The "
            "conversation still works on a branch of its own (lemma/<slug>), "
            "made from this point; without it, from the default branch. Only "
            "with `repository`."
        ),
    )

    @field_validator("repository")
    @classmethod
    def _owner_slash_name(cls, value: str | None) -> str | None:
        if value is not None and parse_repository_name(value) is None:
            raise ValueError("repository must be 'owner/name'")
        return value

    @field_validator("ref")
    @classmethod
    def _checkout_ref(cls, value: str | None) -> str | None:
        if value is not None and not is_checkout_ref(value):
            raise ValueError(
                "ref must be a branch, a tag, a commit or 'pull/<number>/head'"
            )
        return value

    @model_validator(mode="after")
    def _ref_needs_repository(self) -> CreateConversationRequest:
        if self.ref is not None and self.repository is None:
            raise ValueError("ref is only for a conversation with a repository")
        return self

    def project_repo(self) -> ProjectRepo | None:
        if not self.repository:
            return None
        return parse_repository_name(self.repository, ref=self.ref)
