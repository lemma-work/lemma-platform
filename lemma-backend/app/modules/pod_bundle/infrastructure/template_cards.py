"""Reading a shipped template's card off its own files.

A template with no ``role`` block in its ``pod.json`` is still importable by
name; it is simply not on the shelf. One whose block does not parse is left off
too, with a warning, rather than failing the whole shelf for every person who
opens it -- ``test_shipped_templates`` holds every shipped card to the same
model, so that warning is for a template dropped onto a running server.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from lemma_pod_bundle import POD_MANIFEST_FILE, TABLE_DATA_FILE, load_resource_payload
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.log.log import get_logger
from app.modules.pod_bundle.domain.template_card import (
    TemplateCard,
    TemplateNeed,
    TemplateOffer,
    TemplateSkill,
    TemplateTable,
    TemplateWin,
)
from app.modules.pod_bundle.infrastructure.templates import (
    TEMPLATES_ROOT,
    shipped_template_names,
    template_root,
)

logger = get_logger(__name__)

ROLE_KEY = "role"

# The table a scorecard lives in. It is how the role is judged, not work the
# role does, so it is not listed among the tables that arrive.
SCORECARD_TABLE = "scorecard"

# Measures every teammate has; the card lists what is particular to the role.
_SHARED_MEASURE_KIND = "reliability"

# Five fields of digits, ranges, steps, lists or `*`. What a schedule accepts
# beyond that is the schedule's to say when the offer is turned on.
_CRON = r"^[0-9*/,-]+( [0-9*/,-]+){4}$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Need(_Strict):
    connector: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,62}$")
    label: str = Field(min_length=1, max_length=60)


class _Win(_Strict):
    say: str = Field(min_length=1, max_length=240)
    needs: _Need | None = None


class _Offer(_Strict):
    title: str = Field(min_length=1, max_length=60)
    detail: str = Field(min_length=1, max_length=160)
    cron: str = Field(pattern=_CRON)
    instruction: str = Field(min_length=1, max_length=4000)


class RoleBlock(_Strict):
    """The ``role`` block of a template's ``pod.json``: the part of the card
    only a person can write."""

    line: str = Field(min_length=1, max_length=120)
    seed: str = Field(min_length=1, max_length=80)
    # Three at most: a list that scrolls is a spec sheet.
    brings: list[str] = Field(min_length=1, max_length=3)
    wins: list[_Win] = Field(min_length=1, max_length=3)
    offers: list[_Offer] = Field(default_factory=list, max_length=3)
    position: int = 100


def template_cards(root: Path = TEMPLATES_ROOT) -> list[TemplateCard]:
    """Every shipped template that has a card, in shelf order.

    Blocking filesystem work: callers run it through ``run_blocking``.
    """
    cards = []
    for name in shipped_template_names(root):
        directory = template_root(name, root=root)
        if directory is None:
            continue
        try:
            card = read_template_card(name, directory)
        except (OSError, ValueError, ValidationError) as exc:
            logger.warning(
                "pod_bundle.template_cards.role_card_unreadable.skipped",
                template=name,
                error=str(exc),
            )
            continue
        if card is not None:
            cards.append(card)
    return sorted(cards, key=lambda card: (card.position, card.name.lower()))


def read_template_card(name: str, directory: Path) -> TemplateCard | None:
    """The card for one template, or ``None`` when it has no ``role`` block.

    Raises ``ValueError`` / ``ValidationError`` when the block is malformed.
    """
    manifest = json.loads((directory / POD_MANIFEST_FILE).read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get(ROLE_KEY) is None:
        return None
    role = RoleBlock.model_validate(manifest[ROLE_KEY])
    title = manifest.get("name")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("pod.json has no name")
    about = manifest.get("description")
    return TemplateCard(
        template=name,
        name=title.strip(),
        role=role.line,
        about=about.strip() if isinstance(about, str) else "",
        seed=role.seed,
        brings=tuple(role.brings),
        wins=tuple(
            TemplateWin(
                say=win.say,
                needs=(
                    TemplateNeed(connector=win.needs.connector, label=win.needs.label)
                    if win.needs
                    else None
                ),
            )
            for win in role.wins
        ),
        offers=tuple(
            TemplateOffer(
                title=offer.title,
                detail=offer.detail,
                cron=offer.cron,
                instruction=offer.instruction,
            )
            for offer in role.offers
        ),
        judged_on=judged_on(directory),
        skills=template_skills(directory),
        tables=template_tables(directory),
        position=role.position,
    )


def judged_on(directory: Path) -> tuple[str, ...]:
    """The measures the template's scorecard turns on, in its own order,
    leaving out the ones every teammate shares."""
    data = directory / "tables" / SCORECARD_TABLE / TABLE_DATA_FILE
    if not data.is_file():
        return ()
    with data.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    kept = [
        row
        for row in rows
        if (row.get("is_on") or "").strip().lower() == "true"
        and (row.get("kind") or "").strip() != _SHARED_MEASURE_KIND
        and (row.get("measure") or "").strip()
    ]
    kept.sort(key=lambda row: _position(row.get("position")))
    return tuple((row.get("measure") or "").strip() for row in kept)


def template_skills(directory: Path) -> tuple[TemplateSkill, ...]:
    skills_dir = directory / "files" / "skills"
    if not skills_dir.is_dir():
        return ()
    skills = []
    for skill_dir in sorted(path for path in skills_dir.iterdir() if path.is_dir()):
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.is_file():
            continue
        fields = _frontmatter(skill_md.read_text(encoding="utf-8"))
        skills.append(
            TemplateSkill(
                name=fields.get("name") or skill_dir.name,
                description=fields.get("description") or "",
            )
        )
    return tuple(skills)


def template_tables(directory: Path) -> tuple[TemplateTable, ...]:
    tables_dir = directory / "tables"
    if not tables_dir.is_dir():
        return ()
    tables = []
    for table_dir in sorted(path for path in tables_dir.iterdir() if path.is_dir()):
        if table_dir.name == SCORECARD_TABLE:
            continue
        payload = load_resource_payload(
            table_dir, table_dir.name, resource_type="tables"
        )
        config = payload.get("config")
        description = config.get("description") if isinstance(config, dict) else None
        tables.append(
            TemplateTable(
                name=table_dir.name,
                description=description if isinstance(description, str) else "",
            )
        )
    return tuple(tables)


def _position(value: str | None) -> int:
    try:
        return int(value or "")
    except ValueError:
        return 1_000


def _frontmatter(text: str) -> dict[str, str]:
    """A leading ``---`` block of top-level ``key: value`` lines -- the same
    reading the pod's skill catalog gives it."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    fields: dict[str, str] = {}
    for raw in lines[1:]:
        if raw.strip() == "---":
            break
        line = raw.strip()
        if not line or line.startswith("#") or raw.startswith((" ", "\t")):
            continue
        if ":" in line:
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip().strip('"').strip("'")
    return fields
