"""A repository conversation's changes, as its read route answers them."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.modules.agent.domain.conversation_changes import (
    ChangedFile,
    ChangesState,
    ConversationChanges,
    FileStatus,
)


class ConversationChangedFile(BaseModel):
    path: str
    old_path: str | None = Field(
        default=None, description="Where a renamed file was, before it moved."
    )
    status: FileStatus
    additions: int
    deletions: int
    binary: bool = Field(
        description="Git found no lines to count; additions and deletions are 0."
    )

    @classmethod
    def of(cls, changed: ChangedFile) -> ConversationChangedFile:
        return cls(
            path=changed.path,
            old_path=changed.old_path,
            status=changed.status,
            additions=changed.additions,
            deletions=changed.deletions,
            binary=changed.binary,
        )


class ConversationChangesTotals(BaseModel):
    files: int = 0
    additions: int = 0
    deletions: int = 0


class ConversationChangesResponse(BaseModel):
    """What the conversation's checkout holds that its start point does not."""

    state: ChangesState = Field(
        description=(
            "`ready`: read now, and everything below is filled in. "
            "`no_repository`: the conversation was not opened in a repository. "
            "`not_checked_out`: it was, but nothing has made its checkout yet "
            "(no command has run), or the checkout was cleared. "
            "`machine_unavailable`: the machine it works on is asleep -- it is "
            "never woken to answer -- or a run of the conversation may not use "
            "it now. `elsewhere`: its last run worked on its person's own Mac "
            "(a coding agent through Agent Host, or host execution), where the "
            "checkout is."
        )
    )
    branch: str | None = Field(
        default=None, description="The checkout's branch; null when detached."
    )
    base_ref: str | None = Field(
        default=None,
        description=(
            "The start point compared against: `origin/<ref>`, the ref itself "
            "for a tag or a commit, or the branch `origin/HEAD` names (as "
            "`origin/main`). Null when it could not be "
            "found in the checkout; the files are then its uncommitted work only."
        ),
    )
    base_sha: str | None = Field(
        default=None,
        description="The merge base of the checkout's HEAD with `base_ref`.",
    )
    head_sha: str | None = None
    ahead: int = Field(
        default=0, description="Commits on the checkout since `base_sha`."
    )
    files: list[ConversationChangedFile] = Field(
        default_factory=list,
        description=(
            "Every file that differs from `base_sha` in the working tree -- "
            "committed, staged or not -- then the untracked files."
        ),
    )
    totals: ConversationChangesTotals = Field(default_factory=ConversationChangesTotals)
    uncommitted: bool = Field(
        default=False,
        description="Edits not yet committed, or untracked files, are present.",
    )
    patch: str | None = Field(
        default=None,
        description="The unified diff, when asked for with `patch=true`.",
    )
    truncated: bool = Field(
        default=False,
        description=(
            "Something was cut to fit: the patch at `max_patch_bytes`, or the "
            "file list in a very large change."
        ),
    )

    @classmethod
    def of(cls, changes: ConversationChanges) -> ConversationChangesResponse:
        return cls(
            state=changes.state,
            branch=changes.branch,
            base_ref=changes.base_ref,
            base_sha=changes.base_sha,
            head_sha=changes.head_sha,
            ahead=changes.ahead,
            files=[ConversationChangedFile.of(changed) for changed in changes.files],
            totals=ConversationChangesTotals(
                files=len(changes.files),
                additions=changes.additions,
                deletions=changes.deletions,
            ),
            uncommitted=changes.uncommitted,
            patch=changes.patch,
            truncated=changes.truncated,
        )
