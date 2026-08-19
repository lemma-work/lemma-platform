"""Pod file tools present paths in the ``/me/...`` alias form, matching what
``pod_search_files`` already returned for the same file — not the raw user UUID.
"""

from __future__ import annotations

from uuid import UUID

from app.modules.agent.tools.pod.pod_paths import normalize_json_paths, to_me_path

_USER = UUID("2bcdade2-0000-4000-8000-00000000b38d")


def test_personal_root_becomes_me():
    assert to_me_path(f"/{_USER}", _USER) == "/me"


def test_personal_subpath_is_aliased():
    assert to_me_path(f"/{_USER}/tool-audit/x.md", _USER) == "/me/tool-audit/x.md"


def test_a_path_outside_the_personal_root_is_left_alone():
    assert to_me_path("/knowledge/shared.md", _USER) == "/knowledge/shared.md"


def test_another_users_root_is_not_rewritten():
    other = UUID("11111111-0000-4000-8000-000000000002")
    assert to_me_path(f"/{other}/x.md", _USER) == f"/{other}/x.md"


def test_normalize_json_paths_rewrites_nested_tree_nodes():
    tree = {
        "path": f"/{_USER}",
        "children": [
            {"path": f"/{_USER}/a", "parent_path": f"/{_USER}"},
            {"path": "/knowledge/k.md"},
        ],
    }

    normalized = normalize_json_paths(tree, _USER)

    assert normalized["path"] == "/me"
    assert normalized["children"][0]["path"] == "/me/a"
    assert normalized["children"][0]["parent_path"] == "/me"
    assert normalized["children"][1]["path"] == "/knowledge/k.md"
