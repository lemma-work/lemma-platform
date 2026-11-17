"""The browser profile is protected on E2B by where a path *resolves*.

E2B's file SDK follows links on its own, so the files API's spelled-path
check let a link into the profile walk a write through it -- found by QA on a
real E2B machine: ``~/lemma/p -> ~/.lemma/browser/profile``, then
``PUT files:content ~/lemma/p/evil.txt`` landed in the profile.
"""

from __future__ import annotations

import posixpath
import re
import shlex
from types import SimpleNamespace

import pytest

from app.modules.workspace.providers.e2b_paths import refuse_protected
from sandbox_runtime.errors import SandboxRejected
from sandbox_runtime.paths import BROWSER_PROFILE_ROOT, HOME_ROOT

pytestmark = pytest.mark.asyncio

LINK = f"{HOME_ROOT}/lemma/p"


class _Machine:
    """A sandbox whose only command is the resolver's ``readlink -f``,
    answered from a table of links, the way the real one would."""

    def __init__(self, links: dict[str, str]) -> None:
        self._links = links
        self.commands = self

    def _resolve(self, path: str) -> str:
        for link, target in self._links.items():
            if path == link or path.startswith(f"{link}/"):
                return target + path[len(link) :]
        return posixpath.normpath(path)

    async def run(self, script: str, timeout: int) -> SimpleNamespace:
        del timeout
        quoted = re.match(r"readlink -f -- (\S+|'[^']*')", script)
        assert quoted is not None, script
        path = shlex.split(quoted.group(1))[0]
        marker = "link" if path in self._links else "plain"
        return SimpleNamespace(stdout=f"{self._resolve(path)}\n{marker}")


@pytest.mark.parametrize(
    ("path", "follow_last"),
    [
        (f"{LINK}/evil.txt", True),  # a write through the link
        (f"{LINK}/Default", True),  # a mkdir through it
        (f"{LINK}/Default/Cookies", False),  # a delete through it
        (BROWSER_PROFILE_ROOT, False),  # the profile, spelled out
        (f"{HOME_ROOT}/.lemma", False),  # what contains it
    ],
)
async def test_a_change_that_lands_in_the_profile_is_refused(
    path: str, follow_last: bool
) -> None:
    machine = _Machine({LINK: BROWSER_PROFILE_ROOT})

    with pytest.raises(SandboxRejected, match="browser's profile"):
        await refuse_protected(machine, path, follow_last=follow_last)


async def test_removing_the_link_itself_leaves_the_profile_alone() -> None:
    machine = _Machine({LINK: BROWSER_PROFILE_ROOT})

    await refuse_protected(machine, LINK, follow_last=False)


async def test_an_ordinary_file_is_untouched_by_the_rule() -> None:
    machine = _Machine({LINK: BROWSER_PROFILE_ROOT})

    await refuse_protected(machine, f"{HOME_ROOT}/lemma/notes.txt", follow_last=True)


@pytest.mark.parametrize("home", [HOME_ROOT, f"{HOME_ROOT}/.lemma"])
async def test_making_the_home_that_holds_the_profile_is_allowed(home: str) -> None:
    """QA: a session makes ``/home/user`` (``mkdir -p``) before every command,
    and refusing what *contains* the profile refused every command on E2B."""
    machine = _Machine({})

    await refuse_protected(machine, home, follow_last=True)
