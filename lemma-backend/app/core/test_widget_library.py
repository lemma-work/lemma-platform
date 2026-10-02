"""The default widget library: each file is a contract the agent reads and copies.

The library lives in the lemma-widget skill so it is browsable at
``/skills/lemma-widget/library/<service>/<name>.html``. The agent prompt names
every widget in it, and each widget's header names the connector operations its
buttons call. These tests hold all three together, because each drifts silently:
a widget missing from the prompt is never used, and an operation that does not
exist fails only when a person presses the button.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.core.html_document import wrap_html_fragment
from app.core.widget_html_validation import validate_widget_html

REPO_ROOT = Path(__file__).resolve().parents[3]
LIBRARY = REPO_ROOT / "lemma-skills" / "lemma-widget" / "library"
PROMPT = (
    REPO_ROOT
    / "lemma-backend"
    / "app"
    / "modules"
    / "agent"
    / "prompts"
    / "user_interaction.md"
)
NATIVE_APPS = REPO_ROOT / "lemma-backend" / "scripts" / "lemma_apps_config.json"

_HEADER = re.compile(r"\A<!--(.*?)-->", re.DOTALL)
_FIELD = re.compile(r"^(\w+):\s*(.+)$", re.MULTILINE)
_SAMPLE = re.compile(
    r'<script type="application/json" data-lemma-sample>(.*?)</script>', re.DOTALL
)
REQUIRED = {
    "widget",
    "description",
    "connector",
    "kind",
    "operations",
    "data",
    "version",
}

WIDGETS = sorted(LIBRARY.rglob("*.html"))


def _header(path: Path) -> dict[str, str]:
    match = _HEADER.match(path.read_text(encoding="utf-8"))
    assert match, f"{path.name} must open with its header comment"
    return dict(_FIELD.findall(match.group(1)))


def test_the_library_is_not_empty():
    assert len(WIDGETS) >= 11


@pytest.mark.parametrize("path", WIDGETS, ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_each_widget_states_its_contract(path: Path):
    header = _header(path)
    assert REQUIRED <= header.keys(), f"missing: {REQUIRED - header.keys()}"
    # The name is where the file lives, so a copy can be found from its header.
    assert header["widget"] == f"{path.parent.name}/{path.stem}"
    assert header["kind"] in {"http", "composio"}


@pytest.mark.parametrize("path", WIDGETS, ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_each_widget_is_a_valid_fragment_with_a_sample(path: Path):
    source = path.read_text(encoding="utf-8")
    assert validate_widget_html(source) == []
    sample = _SAMPLE.search(source)
    assert sample, "a library widget carries a sample, so it previews in the library"
    json.loads(sample.group(1))


@pytest.mark.parametrize("path", WIDGETS, ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_each_widget_calls_only_what_its_header_declares(path: Path):
    """Every `lemma.act` names this widget's connector and a declared operation."""
    header = _header(path)
    source = path.read_text(encoding="utf-8")
    declared = {op.strip() for op in header["operations"].split(",")}
    called = set(
        re.findall(
            r'lemma\.act\("' + re.escape(header["connector"]) + r'",\s*"([^"]+)"',
            source,
        )
    )
    # Some widgets route their calls through a local helper; the operation
    # names still appear as string literals in the file.
    called |= {op for op in declared if f'"{op}"' in source}
    assert called == declared
    assert re.findall(r'lemma\.act\("([^"]+)"', source) == [header["connector"]] * len(
        re.findall(r'lemma\.act\("([^"]+)"', source)
    ), "a widget acts on one service"
    assert f'kind: "{header["kind"]}"' in source


def _native_operations() -> dict[str, set[str]]:
    apps = json.loads(NATIVE_APPS.read_text(encoding="utf-8"))
    return {
        app["name"]: {op["name"] for op in app.get("static_operations") or []}
        for app in apps
    }


@pytest.mark.parametrize(
    "path",
    [p for p in WIDGETS if _header(p)["kind"] == "http"],
    ids=lambda p: f"{p.parent.name}/{p.stem}",
)
def test_native_operations_exist_in_the_catalog(path: Path):
    """Lemma's own connectors ship their operations in the repo, so a name a
    widget calls that is not there is a button that can never work."""
    header = _header(path)
    native = _native_operations()
    assert header["connector"] in native
    missing = {op.strip() for op in header["operations"].split(",")} - native[
        header["connector"]
    ]
    assert not missing


def test_the_prompt_lists_exactly_the_library():
    """The prompt is how an agent knows a widget exists. One added to the
    folder and not the list is never used; one listed and deleted fails."""
    prompt = PROMPT.read_text(encoding="utf-8")
    section = prompt.split("### The widget library", 1)[1]
    listed: dict[str, list[str]] = {}
    for service, names in re.findall(r"^- ([a-z0-9-]+): (.+)$", section, re.MULTILINE):
        listed[service] = [name.strip() for name in names.split(",")]
    on_disk: dict[str, list[str]] = {}
    for path in WIDGETS:
        on_disk.setdefault(path.parent.name, []).append(path.stem)
    assert {k: sorted(v) for k, v in listed.items()} == {
        k: sorted(v) for k, v in on_disk.items()
    }


@pytest.mark.parametrize("path", WIDGETS, ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_each_widget_renders_with_given_data(path: Path):
    """Passed `data` reaches the page as the block `lemma.data` reads first."""
    sample = json.loads(_SAMPLE.search(path.read_text(encoding="utf-8")).group(1))
    page = wrap_html_fragment(path.read_text(encoding="utf-8"), data=sample)
    given = page.index('<script type="application/json" data-lemma-widget-data>')
    assert given < page.index('<script type="application/json" data-lemma-sample>')
