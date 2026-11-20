"""The marked blocks plugins keep in the shared memory indexes, and the one rule
for an agent's write to an index that holds them.

A plugin tells every agent in a Lemma that it is installed with a short block in
``/memory/AGENTS.md`` (and, for a personal one, ``/me/AGENTS.md``)::

    <!-- plugin:tasks -->
    - Tasks is installed: ...
    <!-- /plugin:tasks -->

The plugin writes it from a function at set-up, through the datastore file API,
and only ever replaces the text between its own markers. Agents write the same
two files whole -- the memory keeper after every turn, the assistant whenever it
tidies its notes -- and a model rewriting an index paraphrases what it reads.
So an agent's write to these files must carry every complete block over byte
for byte (anywhere in the file), and may not add one: a block an agent made up
would read to every later run as a plugin's own word. The plugin's writes do
not come through the agent's tools, so its own add, update and remove are
untouched by this.

Only complete blocks are held: an opening marker and, before the next opening
marker for the same plugin, its closing one. An opening whose closing marker
was lost is left to whoever edits next -- what follows it may be the team's
own notes, and locking it would lock the file. This is the parse the plugins'
own writer uses, so both sides agree on what a block is.
"""

from __future__ import annotations

import re

from app.modules.agent.domain.agent_memory_paths import (
    PERSONAL_MEMORY_INDEX,
    POD_MEMORY_INDEX,
)

#: The indexes plugins write blocks into, in the ``/me/...`` alias form.
PLUGIN_BLOCK_INDEXES = frozenset({POD_MEMORY_INDEX, PERSONAL_MEMORY_INDEX})

_OPENING = re.compile(r"<!--\s*plugin:([a-z0-9][a-z0-9-]*)\s*-->")


def _closing(slug: str) -> re.Pattern[str]:
    return re.compile(r"<!--\s*/plugin:" + re.escape(slug) + r"\s*-->")


def find_plugin_blocks(text: str) -> dict[str, tuple[str, ...]]:
    """Each plugin's complete blocks in ``text``, markers included, in order.

    A plugin normally has one; a file edited by hand can hold copies, and each
    is kept as written.
    """
    openings: dict[str, list[int]] = {}
    for match in _OPENING.finditer(text):
        openings.setdefault(match.group(1), []).append(match.start())
    blocks: dict[str, tuple[str, ...]] = {}
    for slug, starts in openings.items():
        closing = _closing(slug)
        found: list[str] = []
        for i, start in enumerate(starts):
            stop = starts[i + 1] if i + 1 < len(starts) else len(text)
            end = closing.search(text, start, stop)
            if end is not None:
                found.append(text[start : end.end()])
        if found:
            blocks[slug] = tuple(found)
    return blocks


def _block_set(text: str) -> set[tuple[str, str]]:
    return {
        (slug, block)
        for slug, blocks in find_plugin_blocks(text).items()
        for block in blocks
    }


def check_blocks_kept(before: str, after: str) -> str | None:
    """``None`` when ``after`` keeps ``before``'s plugin blocks, else what to do.

    Kept means every complete block in ``before`` is in ``after`` exactly, and
    ``after`` has no block ``before`` did not. The answer is written for the
    model that made the write, which has to put it right in its next call.
    """
    was = _block_set(before)
    now = _block_set(after)
    changed = sorted({slug for slug, _ in was - now})
    added = sorted({slug for slug, _ in now - was} - set(changed))
    if not changed and not added:
        return None
    problems: list[str] = []
    if changed:
        named = ", ".join(f"`<!-- plugin:{slug} -->`" for slug in changed)
        problems.append(
            f"This write changes or removes the block that starts {named}. "
            "A plugin wrote that block and keeps it up to date: copy everything "
            "from its opening marker to its closing marker exactly as it is in "
            "the file now, and write your notes above or below it."
        )
    if added:
        named = ", ".join(f"`<!-- plugin:{slug} -->`" for slug in added)
        problems.append(
            f"This write adds a block marked {named}, and only the plugin "
            "itself writes those. Write the note as an ordinary line, without "
            "the plugin markers."
        )
    return " ".join(problems) + " Nothing was written."


__all__ = [
    "PLUGIN_BLOCK_INDEXES",
    "check_blocks_kept",
    "find_plugin_blocks",
]
