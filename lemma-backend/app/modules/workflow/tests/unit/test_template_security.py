from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "template_name", ["workflow_view.html", "workflow_run_view.html"]
)
def test_remote_workflow_scripts_are_integrity_pinned(template_name: str) -> None:
    template = Path(__file__).resolve().parents[2] / "templates" / template_name
    remote_scripts = [
        line.strip()
        for line in template.read_text().splitlines()
        if "<script" in line and 'src="https://' in line
    ]

    assert len(remote_scripts) == 3
    assert all('integrity="sha384-' in line for line in remote_scripts)
    assert all('crossorigin="anonymous"' in line for line in remote_scripts)
