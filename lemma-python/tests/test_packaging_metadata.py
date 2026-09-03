"""What the PyPI page for ``lemma-sdk`` says about itself.

Project URLs and classifiers are the only navigation a stranger gets from a
package page: without them there is no "view source", no "report a bug", and no
supported-Python row, and nothing in a build or a release fails to point that
out. The npm package next door declares all of it, so these tests keep the two
from drifting apart again.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

_SDK_ROOT = Path(__file__).resolve().parents[1]
_PROJECT = tomllib.loads((_SDK_ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
    "project"
]


def test_project_urls_point_at_source_and_issues() -> None:
    urls = _PROJECT.get("urls") or {}

    assert {"Homepage", "Repository", "Issues"} <= set(urls)
    for name, url in urls.items():
        assert url.startswith("https://"), f"{name} is not an https URL: {url}"


def test_classifiers_declare_the_python_requires_python_allows() -> None:
    """A resolver reads ``requires-python``; a person reads the classifier. They
    have to agree, or the page advertises a version the wheel refuses."""
    classifiers = _PROJECT.get("classifiers") or []

    assert "Programming Language :: Python :: 3.14" in classifiers
    assert ">=3.14" in _PROJECT["requires-python"]
    # 3.15 is excluded by requires-python, so claiming it here would be a lie.
    assert "Programming Language :: Python :: 3.15" not in classifiers


def test_license_is_declared_once_and_matches_the_classifier_free_form() -> None:
    """setuptools rejects a License classifier alongside a PEP 639 expression,
    and the expression is the one that reaches the wheel's metadata."""
    assert _PROJECT["license"]
    assert not [c for c in _PROJECT.get("classifiers") or [] if c.startswith("License")]
