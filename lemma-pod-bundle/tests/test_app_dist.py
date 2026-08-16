"""Whether an exported app build may be deployed into another pod as-is.

The check can only ever prove NON-portability, so every uncertain answer has to
fall on the "rebuild it" side: a false "not portable" costs a sandbox build we
would have done anyway, a false "portable" ships an app pointed at the pod it
was exported from.
"""

from __future__ import annotations

import io
import zipfile

from lemma_pod_bundle import dist_is_portable

POD = "019ba7e8-5115-7000-8000-000000000002"


def _dist(**members: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in members.items():
            archive.writestr(name.replace("__", "/"), content)
    return buffer.getvalue()


def test_a_build_that_mentions_no_pod_is_portable():
    assert dist_is_portable(
        _dist(**{"index.html": "<html></html>", "assets__app.js": "new LemmaClient()"}),
        pod_id=POD,
    )


def test_a_build_that_baked_the_pod_id_in_is_not():
    assert not dist_is_portable(
        _dist(**{"assets__app.js": f'const POD="{POD}";'}), pod_id=POD
    )


def test_a_pod_id_stripped_of_its_hyphens_is_still_found():
    """A minifier or a URL path can re-emit the id without hyphens. Matching only
    the canonical form called such a build portable and shipped it pointed at
    the source pod."""
    assert not dist_is_portable(
        _dist(**{"assets__app.js": f'fetch("/pods/{POD.replace("-", "")}/x")'}),
        pod_id=POD,
    )


def test_case_does_not_hide_a_baked_id():
    assert not dist_is_portable(
        _dist(**{"assets__app.js": f'const POD="{POD.upper()}";'}), pod_id=POD
    )


def test_an_unreadable_archive_is_rebuilt():
    assert not dist_is_portable(b"not a zip at all", pod_id=POD)


def test_an_absent_pod_id_cannot_clear_a_build():
    # With nothing to search for, every build would look clean.
    assert not dist_is_portable(_dist(**{"index.html": "x"}), pod_id="")


def test_an_implausibly_large_member_is_refused_rather_than_read():
    """The importer reads bundles it did not produce, so a member claiming more
    than the cap is rebuilt rather than decompressed."""
    from lemma_pod_bundle.app_dist import _MAX_SCANNED_MEMBER_BYTES

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        # Compresses to almost nothing, but declares far more than the cap.
        archive.writestr("assets/app.js", "0" * (_MAX_SCANNED_MEMBER_BYTES + 1))
    assert not dist_is_portable(buffer.getvalue(), pod_id=POD)


def test_binary_assets_are_not_scanned():
    # Fonts and images cannot carry a usable id, and reading them wastes the
    # whole budget on megabytes that never match.
    assert dist_is_portable(
        _dist(**{"assets__logo.woff2": POD, "index.html": "<html></html>"}),
        pod_id=POD,
    )
