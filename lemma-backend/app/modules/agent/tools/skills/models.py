from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from app.modules.agent.tools.context import BaseToolResponse


class ListSkillsRequest(BaseModel):
    """Empty request model for listing workspace skills."""


class SkillLookupRequest(BaseModel):
    name: str = Field(description="Skill name to load, for example `skill-creator`.")
    resource_path: Optional[str] = Field(
        default=None,
        description=(
            "Optional relative path to a file inside the skill directory. Omit to "
            "load `SKILL.md` (the response also lists the skill's resource paths); "
            "pass a path from that list to load that resource instead. Must not be "
            "absolute."
        ),
    )


class SkillSummary(BaseModel):
    name: str = Field(description="Unique skill name.")
    description: str = Field(description="Short description of what the skill does.")
    path: str = Field(
        description="Backing path to the skill's `SKILL.md` file. Pod skills use `/skills/...` paths."
    )
    workspace_path: str = Field(
        description="Workspace-visible absolute path to the skill's `SKILL.md` file."
    )
    workspace_dir: str = Field(
        description="Workspace-visible absolute path to the root directory for this skill."
    )


class SkillResourceSummary(BaseModel):
    path: str = Field(
        description="Relative path to the resource inside the skill directory."
    )
    workspace_path: str = Field(
        description="Workspace-visible absolute path to the resource file."
    )
    kind: str = Field(
        description="High-level resource type such as `text`, `script`, or `file`."
    )
    executable: str = Field(
        description="String flag returned by the loader indicating whether the resource is executable."
    )


class SkillListResult(BaseToolResponse):
    skills: list[SkillSummary] = Field(
        default_factory=list,
        description="Available workspace skills.",
    )


class SkillContentResult(BaseToolResponse):
    name: Optional[str] = Field(
        default=None,
        description="Skill name that was loaded.",
    )
    resource_path: Optional[str] = Field(
        default=None,
        description="Relative resource path when the loaded content is a resource, not `SKILL.md`.",
    )
    content: Optional[str] = Field(
        default=None,
        description="Text content loaded from the requested skill file.",
    )
    resources: list[SkillResourceSummary] = Field(
        default_factory=list,
        description=(
            "Extra files inside the skill directory (populated when loading "
            "`SKILL.md`). Pass a resource's `path` back as `resource_path` to load it."
        ),
    )
