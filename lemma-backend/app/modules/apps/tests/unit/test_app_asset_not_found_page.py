"""A link an app does not serve has to end somewhere a person can act.

An app rendering markdown whose links point at pod files -- a real one, whose
markdown said ``library/rust/rustbook-ownership.md`` -- has those links resolved
against the app's own origin. The app does not serve them, so every click ended
on a raw JSON error in a window with no navigation and no way back, which reads
as the app being broken rather than the link pointing somewhere else.

What is asserted here is the whole contract: a person gets a page and an offer,
code still gets its JSON, and the offer is never made in a way that would let a
stranger use it to learn what a pod holds.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.apps.api.asset_not_found_page import (
    looks_like_a_document,
    render_asset_not_found_page,
    workspace_file_url,
)
from app.modules.apps.api.controllers.public_app_controller import _is_navigation
from app.modules.apps.domain.errors import AppAssetNotFoundError, AppNotFoundError
from app.modules.apps.services.app_storage_phase import (
    AppStoragePhase,
    _AssetReadInputs,
)


class _EmptyStorage:
    """An app bundle with an entrypoint and nothing else in it."""

    async def read_file(self, key: str) -> bytes:
        if key.endswith("index.html"):
            return b"<!doctype html><title>app</title>"
        raise FileNotFoundError(key)


class _Request:
    def __init__(self, accept: str):
        self.headers = {"accept": accept}


def _phase() -> AppStoragePhase:
    return AppStoragePhase(file_manager_factory=lambda _app_id: _EmptyStorage())


@pytest.mark.asyncio
async def test_a_missing_file_link_names_the_pod_it_could_have_come_from():
    """The error carries what the alternative offer needs, or there is none."""
    pod_id = uuid4()
    inputs = _AssetReadInputs(
        app_id=uuid4(),
        pod_id=pod_id,
        dist_root_path="releases/v1/dist/",
        normalized_asset_path="library/rust/rustbook-ownership.md",
        quoted_etag=None,
    )

    with pytest.raises(AppAssetNotFoundError) as raised:
        await _phase().read_asset(inputs)

    assert raised.value.pod_id == pod_id
    assert raised.value.asset_path == "library/rust/rustbook-ownership.md"
    # Still the same 404 to every existing caller: the richer type is a
    # subclass, so nothing that catches the old one stops catching it.
    assert isinstance(raised.value, AppNotFoundError)
    assert raised.value.status_code == 404


@pytest.mark.asyncio
async def test_a_client_side_route_is_still_served_the_app():
    """The SPA fallback is untouched -- only paths with a suffix reach the page."""
    document = await _phase().read_asset(
        _AssetReadInputs(
            app_id=uuid4(),
            pod_id=uuid4(),
            dist_root_path="releases/v1/dist/",
            normalized_asset_path="library/rust",
            quoted_etag=None,
        )
    )
    assert document.is_entrypoint is True


def test_only_a_navigation_is_given_a_page():
    """A fetch for a missing chunk keeps the JSON error it has always had."""
    assert _is_navigation(_Request("text/html,application/xhtml+xml,*/*;q=0.8"))
    assert not _is_navigation(_Request("application/json"))
    assert not _is_navigation(_Request("*/*"))
    assert not _is_navigation(_Request(""))


def test_the_offer_is_made_for_documents_and_withheld_from_build_output():
    """A missing chunk is a broken build; pointing at the workspace is noise."""
    assert looks_like_a_document("library/rust/rustbook-ownership.md")
    assert looks_like_a_document("notes/Q3.PDF")
    assert not looks_like_a_document("assets/index-4f2a.js")
    assert not looks_like_a_document("assets/index.css")


def test_the_workspace_link_is_the_document_view_deep_link():
    """`?file=` is the view's own link, so this adds no second route to keep."""
    pod_id = uuid4()
    url = workspace_file_url(pod_id, "library/rust/rustbook ownership.md")
    assert url is not None
    assert f"/pod/{pod_id}/files?file=" in url
    # Encoded, or a path with a space or an `&` in it silently truncates.
    assert "rustbook%20ownership.md" in url
    assert " " not in url


def test_the_page_offers_the_workspace_and_a_way_back():
    pod_id = uuid4()
    page = render_asset_not_found_page(
        asset_path="library/rust/rustbook-ownership.md",
        pod_id=pod_id,
        app_name="Study Lab",
    )
    assert "library/rust/rustbook-ownership.md" in page
    assert "Study Lab" in page
    assert f"/pod/{pod_id}/files?file=" in page
    assert 'href="/"' in page
    # `_top`, because an app usually runs inside the workspace's own frame and
    # replacing only the frame leaves the workspace nested inside itself.
    assert 'target="_top"' in page


def test_a_missing_build_asset_is_told_the_truth_and_offered_nothing():
    """No pod link for a chunk file: there is nothing in the workspace to open."""
    page = render_asset_not_found_page(
        asset_path="assets/index-4f2a.js",
        pod_id=uuid4(),
        app_name=None,
    )
    assert "assets/index-4f2a.js" in page
    assert "/files?file=" not in page
    assert 'href="/"' in page


def test_a_path_cannot_write_markup_into_the_page():
    """The path is attacker-shaped input: it is echoed, so it must be escaped."""
    page = render_asset_not_found_page(
        asset_path='"><script>alert(1)</script>.md',
        pod_id=uuid4(),
        app_name="<img src=x onerror=alert(2)>",
    )
    assert "<script>alert(1)</script>" not in page
    assert "<img src=x" not in page
    assert "&lt;script&gt;" in page
