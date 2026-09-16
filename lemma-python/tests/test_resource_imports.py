"""Every resource facade imports.

A facade names generated model classes by hand, and the generated models are
imported lazily -- so a facade that names one that does not exist is a module
nobody can import, and nothing says so until somebody uses it. `web_logins`
shipped asking for `WebLoginAuditListResponse` while the generator had produced
`WebLoginAuditResponse`: the whole saved-logins surface was dead on import, and
the only way to find out was to try.

Importing them all is the cheapest possible guard and it covers every facade at
once, including ones added later.
"""

from __future__ import annotations

import importlib
import pkgutil

import pytest

import lemma_sdk.resources as resources


def _resource_modules() -> list[str]:
    return sorted(
        info.name
        for info in pkgutil.iter_modules(resources.__path__)
        if not info.name.startswith("_")
    )


@pytest.mark.parametrize("name", _resource_modules())
def test_a_resource_facade_imports(name: str) -> None:
    importlib.import_module(f"lemma_sdk.resources.{name}")


def test_there_are_resources_to_check() -> None:
    """A guard that silently checks nothing is worse than no guard."""
    assert len(_resource_modules()) > 10
