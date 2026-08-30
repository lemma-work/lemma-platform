"""What an app has to serve before a browser will offer to install it."""

from __future__ import annotations

import io
import json
import math
from uuid import uuid4

import pytest
from PIL import Image

from app.core import app_install
from app.modules.apps.domain.entities import AppEntity
from app.modules.apps.services import app_install_assets as assets
from app.modules.apps.services.app_icon import ICON_SIZES, render_app_icon


def make_app(name: str = "Invoice Tracker", slug: str = "invoice-tracker") -> AppEntity:
    return AppEntity(
        pod_id=uuid4(),
        user_id=uuid4(),
        name=name,
        public_slug=slug,
        description="Track invoices",
    )


def render(app: AppEntity, path: str) -> assets.ReservedAsset:
    name = assets.reserved_asset_name(path)
    assert name is not None, path
    return assets.render_reserved_asset(app, name)


def test_manifest_carries_everything_an_install_offer_requires():
    # Chromium will not fire beforeinstallprompt without all of these, and it
    # fails silently -- the offer simply never appears.
    manifest = assets.build_manifest(make_app())

    assert manifest["name"] == "Invoice Tracker"
    assert manifest["start_url"] == "/"
    assert manifest["scope"] == "/"
    assert manifest["display"] == "standalone"

    sizes = {icon["sizes"] for icon in manifest["icons"]}
    assert {"192x192", "512x512"} <= sizes
    assert all(icon["type"] == "image/png" for icon in manifest["icons"])
    assert all("maskable" in icon["purpose"] for icon in manifest["icons"])


def test_manifest_short_name_fits_under_an_icon():
    manifest = assets.build_manifest(make_app(name="Quarterly Revenue Dashboard"))

    assert manifest["name"] == "Quarterly Revenue Dashboard"
    # Cut at a word, not mid-syllable, and short enough that no launcher has to
    # truncate it again.
    assert manifest["short_name"] == "Quarterly"


def test_manifest_omits_an_absent_description():
    app = make_app()
    app.description = None

    assert "description" not in assets.build_manifest(app)


def test_manifest_is_json_a_browser_will_parse():
    body, media_type, _ = render(make_app(), ".lemma/manifest.webmanifest")

    assert media_type == "application/manifest+json"
    assert json.loads(body)["id"] == "/"


@pytest.mark.parametrize(
    "path",
    [
        ".lemma/manifest.webmanifest",
        ".lemma/sw.js",
        ".lemma/offline.html",
        *[f".lemma/icon-{size}.png" for size in ICON_SIZES],
    ],
)
def test_every_path_the_head_points_at_is_answered(path):
    assert assets.reserved_asset_name(path) is not None


def test_an_apps_own_paths_are_left_alone():
    # An app owns every path on its origin that this module does not claim --
    # including an unrecognised name inside the reserved directory, which is
    # looked up in the build like any other asset rather than 404ing early.
    for path in ("index.html", "assets/app.js", ".lemma/whatever.png", ""):
        assert assets.reserved_asset_name(path) is None


def test_the_tag_tracks_the_app_not_the_build():
    app = make_app()
    tag = assets.reserved_asset_etag(app, "manifest.webmanifest")

    # Nothing here is derived from a release, so rebuilding cannot invalidate
    # an icon somebody already has on a home screen.
    assert assets.reserved_asset_etag(make_app(), "manifest.webmanifest") == tag
    # Renaming is the one thing that genuinely changes both the manifest and
    # the letter on the icon.
    assert (
        assets.reserved_asset_etag(make_app(name="Expenses"), "manifest.webmanifest")
        != tag
    )
    assert assets.reserved_asset_etag(app, "sw.js") != tag


def test_service_worker_may_claim_the_whole_origin():
    body, media_type, headers = render(make_app(), ".lemma/sw.js")

    assert "javascript" in media_type
    # Registered for "/" from a script one directory down, which the browser
    # refuses outright without this header.
    assert headers == {"Service-Worker-Allowed": "/"}
    assert app_install.OFFLINE_PATH in body.decode("utf-8")


def test_service_worker_answers_navigations_and_nothing_else():
    body = render(make_app(), ".lemma/sw.js").content.decode("utf-8")

    # The offline answer is what makes the browser call the app installable.
    assert 'event.request.mode !== "navigate"' in body
    # Caching an app asset would serve last week's build to whoever installed
    # it, because a new release lands whenever the author rebuilds.
    assert "cache.add(OFFLINE)" in body
    assert "addAll" not in body


@pytest.mark.parametrize("size", ICON_SIZES)
def test_icon_is_a_square_png_at_the_size_the_head_asked_for(size):
    body, media_type, _ = render(make_app(), f".lemma/icon-{size}.png")

    assert media_type == "image/png"
    with Image.open(io.BytesIO(body)) as image:
        assert image.size == (size, size)
        assert image.format == "PNG"


def test_icon_keeps_its_glyph_inside_the_maskable_safe_zone():
    # Declared "any maskable", so Android crops to the central 80% circle. A
    # letter drawn outside it comes back with its corners shaved off.
    size = 512
    with Image.open(
        io.BytesIO(render_app_icon(name="Wq", slug="wq", size=size))
    ) as raw:
        image = raw.convert("RGB")
    plate = image.getpixel((0, 0))
    centre = size / 2
    furthest = max(
        math.dist((x, y), (centre, centre))
        for y in range(size)
        for x in range(size)
        if image.getpixel((x, y)) != plate
    )

    assert furthest < size * 0.4


def test_icons_are_stable_per_app_and_distinct_between_apps():
    # Stable: the icon on someone's home screen must not change under them.
    assert render_app_icon(name="Standup", slug="standup", size=192) == render_app_icon(
        name="Standup", slug="standup", size=192
    )
    # Distinct: a home screen of pod apps should not be one square repeated.
    assert render_app_icon(name="Standup", slug="standup", size=192) != render_app_icon(
        name="Expenses", slug="expenses", size=192
    )


def test_icon_falls_back_to_the_slug_when_the_name_has_no_ascii():
    # The container's DejaVu has no CJK, so drawing the name's own first
    # character would render a tofu box.
    assert render_app_icon(name="日本語", slug="reports", size=192) == render_app_icon(
        name="R", slug="reports", size=192
    )
