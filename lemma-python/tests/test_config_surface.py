"""What `lemma_sdk.config` promises, and the one thing another repo scrapes.

`config.py` is in a published `py.typed` package, so every module-scope name it
defines is a promise. Nothing declared which promises were meant: four functions
had no caller anywhere, and seven more were implementation only this module used.
They were public because they were defined, and stayed public because nobody
could tell the difference.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import lemma_sdk.config as config

_CONFIG_SOURCE = Path(config.__file__)


def _module_scope_public_names() -> set[str]:
    tree = ast.parse(_CONFIG_SOURCE.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            if not node.name.startswith("_"):
                names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Name)
                    and not target.id.startswith("_")
                    and target.id.isupper()
                ):
                    names.add(target.id)
    return names


def test_every_public_name_is_one_the_module_meant_to_publish():
    """`__all__` and the module's actual public names agree.

    Both directions on purpose. A name missing from `__all__` is a promise made
    by accident; a name in `__all__` that no longer exists is a promise this
    package cannot keep, and `from lemma_sdk.config import *` would raise on it.
    """
    declared = set(config.__all__)
    actual = _module_scope_public_names()

    assert actual - declared == set(), (
        f"public but undeclared: {sorted(actual - declared)}. Add it to "
        "__all__ if it is API, or prefix it with an underscore if it is not."
    )
    assert declared - actual == set(), (
        f"declared but missing: {sorted(declared - actual)}. `import *` would "
        "raise on these."
    )


def test_desktop_local_bases_stay_where_this_script_can_find_them():
    """`scripts/check_local_domain_consistency.py` reads this by name, from text.

    It has to. CI runs that script with whatever `python` the runner provides,
    and this module is 3.14 source using PEP 758 `except A, B:` — an old
    interpreter can neither import it nor `ast.parse` it. So the coupling is a
    regex over source, and the rename that breaks it would otherwise surface as
    "the check is broken" in a repo-level script rather than here, where the
    rename happened.

    The private name is deliberate: these bases are not API. What this asserts is
    only that they keep the name and shape that consumer matches on.
    """
    source = _CONFIG_SOURCE.read_text(encoding="utf-8")

    assert hasattr(config, "_DESKTOP_LOCAL_BASES"), (
        "`_DESKTOP_LOCAL_BASES` is gone. `scripts/check_local_domain_consistency.py` "
        "reads it by name to check the Rust shell, the Tauri capability file and "
        "this SDK agree on which domains are desktop-local. Update that script in "
        "the same change."
    )
    assert re.search(r"_DESKTOP_LOCAL_BASES\s*=\s*[\(\[{]([^)\]}]*)[\)\]}]", source), (
        "`_DESKTOP_LOCAL_BASES` is no longer a literal container of strings. "
        "`scripts/check_local_domain_consistency.py` matches it with a regex "
        "because it cannot import this module; building it dynamically would "
        "leave that check silently reading nothing."
    )
    assert all(isinstance(base, str) for base in config._DESKTOP_LOCAL_BASES)
