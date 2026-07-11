from __future__ import annotations

import io
from pathlib import Path
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from fastapi import status

from app.core.config import settings
from app.modules.test_support.e2e_authz import (
    create_role_visibility_context,
    item_names,
)

pytestmark = pytest.mark.e2e


def build_dist_archive(marker: str) -> bytes:
    buffer = io.BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(
            "index.html",
            f"""
<!doctype html>
<html>
  <body>
    <div id="root">{marker}</div>
    <script type="module" src="/assets/app.js"></script>
  </body>
</html>
""".strip(),
        )
        archive.writestr("assets/app.js", f"console.log('{marker}')")
    return buffer.getvalue()


def build_source_archive(marker: str) -> bytes:
    buffer = io.BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(
            "src/index.ts",
            f"export const appMarker = '{marker}';\n",
        )
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_app_assets_support_private_and_public_asset_routes(
    async_client,
    authenticated_client,
    test_pod,
    monkeypatch,
):
    # Pin both inputs the app URL is built from so the assertion is hermetic and
    # independent of the local .env (the scheme is taken from api_url's scheme,
    # so a developer's https ngrok api_url must not leak into this test).
    monkeypatch.setattr(settings, "api_url", "http://api.apps.test")
    monkeypatch.setattr(settings, "app_base_domain", "apps.test")
    expected_url = f"http://{{slug}}.{settings.app_base_domain}"

    pod_id = test_pod["id"]
    app_name = f"app_public_{uuid4().hex[:8]}"
    marker = f"PUBLIC_APP_MARKER_{uuid4().hex[:8]}"
    public_slug = f"public-app-{uuid4().hex[:8]}"

    create_res = await authenticated_client.post(
        f"/pods/{pod_id}/apps",
        json={
            "name": app_name,
            "public_slug": public_slug,
            "description": "public preview check",
        },
    )
    assert create_res.status_code == status.HTTP_201_CREATED, create_res.text
    assert create_res.json()["url"] == expected_url.format(slug=public_slug)

    upload_res = await authenticated_client.post(
        f"/pods/{pod_id}/apps/{app_name}/bundle",
        files={
            "dist_archive": ("dist.zip", build_dist_archive(marker), "application/zip"),
        },
    )
    assert upload_res.status_code == status.HTTP_200_OK, upload_res.text

    unlisted_public_res = await async_client.get(
        "/public/apps",
        headers={"X-App-Public-Slug": public_slug},
    )
    assert unlisted_public_res.status_code == status.HTTP_200_OK, unlisted_public_res.text
    assert marker in unlisted_public_res.text

    publish_res = await authenticated_client.patch(
        f"/pods/{pod_id}/apps/{app_name}",
        json={"visibility": "PUBLIC"},
    )
    assert publish_res.status_code == status.HTTP_200_OK, publish_res.text
    assert publish_res.json()["url"] == expected_url.format(slug=public_slug)

    asset_res = await authenticated_client.get(f"/pods/{pod_id}/apps/{app_name}/assets")
    assert asset_res.status_code == status.HTTP_200_OK, asset_res.text
    assert marker in asset_res.text
    assert asset_res.headers["etag"]

    not_modified_res = await authenticated_client.get(
        f"/pods/{pod_id}/apps/{app_name}/assets",
        headers={"If-None-Match": asset_res.headers["etag"]},
    )
    assert not_modified_res.status_code == status.HTTP_304_NOT_MODIFIED, not_modified_res.text

    spa_fallback_res = await authenticated_client.get(
        f"/pods/{pod_id}/apps/{app_name}/assets/page"
    )
    assert spa_fallback_res.status_code == status.HTTP_200_OK, spa_fallback_res.text
    assert marker in spa_fallback_res.text

    missing_res = await authenticated_client.get(
        f"/pods/{pod_id}/apps/{app_name}/assets/assets/missing.js"
    )
    assert missing_res.status_code == status.HTTP_404_NOT_FOUND, missing_res.text

    js_res = await authenticated_client.get(
        f"/pods/{pod_id}/apps/{app_name}/assets/assets/app.js"
    )
    assert js_res.status_code == status.HTTP_200_OK, js_res.text
    assert marker in js_res.text
    assert "immutable" in js_res.headers["cache-control"]

    public_res = await async_client.get(
        "/public/apps",
        headers={"X-App-Public-Slug": public_slug},
    )
    assert public_res.status_code == status.HTTP_200_OK, public_res.text
    assert marker in public_res.text

    public_spa_fallback_res = await async_client.get(
        "/public/apps/page",
        headers={"X-App-Public-Slug": public_slug},
    )
    assert public_spa_fallback_res.status_code == status.HTTP_200_OK, public_spa_fallback_res.text
    assert marker in public_spa_fallback_res.text

    public_missing_res = await async_client.get(
        "/public/apps/assets/missing.js",
        headers={"X-App-Public-Slug": public_slug},
    )
    assert public_missing_res.status_code == status.HTTP_404_NOT_FOUND, public_missing_res.text

    public_js_res = await async_client.get(
        "/public/apps/assets/app.js",
        headers={"X-App-Public-Slug": public_slug},
    )
    assert public_js_res.status_code == status.HTTP_200_OK, public_js_res.text
    assert marker in public_js_res.text

    # Host-based routing: requests to `<slug>.<app_base_domain>` are rewritten
    # onto /public/apps by AppHostRoutingMiddleware, no explicit slug header.
    app_host = f"{public_slug}.{settings.app_base_domain}"
    host_root_res = await async_client.get("/", headers={"host": app_host})
    assert host_root_res.status_code == status.HTTP_200_OK, host_root_res.text
    assert marker in host_root_res.text

    host_js_res = await async_client.get("/assets/app.js", headers={"host": app_host})
    assert host_js_res.status_code == status.HTTP_200_OK, host_js_res.text
    assert marker in host_js_res.text

    # The bare base domain (the main API host) is not treated as an app.
    apex_res = await async_client.get("/", headers={"host": settings.app_base_domain})
    assert apex_res.status_code != status.HTTP_200_OK or marker not in apex_res.text

    public_not_modified_res = await async_client.get(
        "/public/apps",
        headers={
            "X-App-Public-Slug": public_slug,
            "If-None-Match": asset_res.headers["etag"],
        },
    )
    assert public_not_modified_res.status_code == status.HTTP_304_NOT_MODIFIED


@pytest.mark.asyncio
async def test_app_list_and_access_respects_pod_roles(
    authenticated_client,
    async_client,
    fixed_test_org,
):
    ctx = await create_role_visibility_context(
        authenticated_client,
        async_client,
        fixed_test_org,
        pod_name_prefix="app-visibility",
        custom_role="APP_REVIEWERS",
    )
    pod_id = ctx["pod_id"]
    default_name = f"default_app_{uuid4().hex[:8]}"
    editor_name = f"editor_app_{uuid4().hex[:8]}"
    custom_name = f"custom_app_{uuid4().hex[:8]}"

    apps: dict[str, dict] = {}
    for name, visibility in [
        (default_name, None),
        (editor_name, "RESTRICTED"),
        (custom_name, "RESTRICTED"),
    ]:
        payload = {"name": name}
        if visibility is not None:
            payload["visibility"] = visibility
        response = await authenticated_client.post(
            f"/pods/{pod_id}/apps",
            json=payload,
        )
        assert response.status_code == status.HTTP_201_CREATED, response.text
        apps[name] = response.json()

    editor_grant = await authenticated_client.put(
        f"/pods/{pod_id}/roles/POD_EDITOR/permissions",
        json={
            "grants": [
                {
                    "resource_type": "app",
                    "resource_name": apps[editor_name]["name"],
                    "permission_ids": ["app.read", "app.update"],
                }
            ]
        },
    )
    assert editor_grant.status_code == status.HTTP_200_OK, editor_grant.text
    custom_grant = await authenticated_client.put(
        f"/pods/{pod_id}/roles/{ctx['custom_role']}/permissions",
        json={
            "grants": [
                {
                    "resource_type": "app",
                    "resource_name": apps[custom_name]["name"],
                    "permission_ids": ["app.read"],
                }
            ]
        },
    )
    assert custom_grant.status_code == status.HTTP_200_OK, custom_grant.text

    viewer_list = await async_client.get(
        f"/pods/{pod_id}/apps",
        headers=ctx["viewer_headers"],
    )
    assert viewer_list.status_code == status.HTTP_200_OK, viewer_list.text
    assert item_names(viewer_list.json()) == {default_name}

    editor_list = await async_client.get(
        f"/pods/{pod_id}/apps",
        headers=ctx["editor_headers"],
    )
    assert editor_list.status_code == status.HTTP_200_OK, editor_list.text
    assert item_names(editor_list.json()) == {default_name, editor_name}
    editor_items = {item["name"]: item for item in editor_list.json()["items"]}
    assert set(editor_items[default_name]["allowed_actions"]) == {
        "app.read",
        "app.update",
        "app.publish",
    }
    assert set(editor_items[editor_name]["allowed_actions"]) == {
        "app.read",
        "app.update",
    }
    editor_get_default = await async_client.get(
        f"/pods/{pod_id}/apps/{default_name}",
        headers=ctx["editor_headers"],
    )
    assert editor_get_default.status_code == status.HTTP_200_OK, editor_get_default.text
    assert set(editor_get_default.json()["allowed_actions"]) == {
        "app.read",
        "app.update",
        "app.publish",
    }
    editor_get_restricted = await async_client.get(
        f"/pods/{pod_id}/apps/{editor_name}",
        headers=ctx["editor_headers"],
    )
    assert editor_get_restricted.status_code == status.HTTP_200_OK, (
        editor_get_restricted.text
    )
    assert set(editor_get_restricted.json()["allowed_actions"]) == {
        "app.read",
        "app.update",
    }

    custom_list = await async_client.get(
        f"/pods/{pod_id}/apps",
        headers=ctx["custom_headers"],
    )
    assert custom_list.status_code == status.HTTP_200_OK, custom_list.text
    assert item_names(custom_list.json()) == {default_name, custom_name}
    custom_items = {item["name"]: item for item in custom_list.json()["items"]}
    assert set(custom_items[default_name]["allowed_actions"]) == {"app.read"}
    assert set(custom_items[custom_name]["allowed_actions"]) == {"app.read"}
    custom_get_restricted = await async_client.get(
        f"/pods/{pod_id}/apps/{custom_name}",
        headers=ctx["custom_headers"],
    )
    assert custom_get_restricted.status_code == status.HTTP_200_OK, (
        custom_get_restricted.text
    )
    assert set(custom_get_restricted.json()["allowed_actions"]) == {"app.read"}

    viewer_get_restricted = await async_client.get(
        f"/pods/{pod_id}/apps/{editor_name}",
        headers=ctx["viewer_headers"],
    )
    assert viewer_get_restricted.status_code == status.HTTP_403_FORBIDDEN

    viewer_edit_default = await async_client.patch(
        f"/pods/{pod_id}/apps/{default_name}",
        json={"description": "viewer edit"},
        headers=ctx["viewer_headers"],
    )
    assert viewer_edit_default.status_code == status.HTTP_403_FORBIDDEN

    custom_edit_custom = await async_client.patch(
        f"/pods/{pod_id}/apps/{custom_name}",
        json={"description": "custom viewer edit"},
        headers=ctx["custom_headers"],
    )
    assert custom_edit_custom.status_code == status.HTTP_403_FORBIDDEN

    editor_edit_restricted = await async_client.patch(
        f"/pods/{pod_id}/apps/{editor_name}",
        json={"description": "editor edit"},
        headers=ctx["editor_headers"],
    )
    assert editor_edit_restricted.status_code == status.HTTP_200_OK
    assert set(editor_edit_restricted.json()["allowed_actions"]) == {
        "app.read",
        "app.update",
    }


@pytest.mark.asyncio
async def test_create_app_rejects_duplicate_public_slug(
    authenticated_client,
    test_pod,
):
    pod_id = test_pod["id"]
    shared_slug = f"shared-app-{uuid4().hex[:8]}"

    first = await authenticated_client.post(
        f"/pods/{pod_id}/apps",
        json={
            "name": f"app_one_{uuid4().hex[:8]}",
            "public_slug": shared_slug,
        },
    )
    assert first.status_code == status.HTTP_201_CREATED, first.text

    second = await authenticated_client.post(
        f"/pods/{pod_id}/apps",
        json={
            "name": f"app_two_{uuid4().hex[:8]}",
            "public_slug": shared_slug,
        },
    )
    assert second.status_code == status.HTTP_409_CONFLICT, second.text
    assert second.json()["code"] == "APP_CONFLICT"


@pytest.mark.asyncio
async def test_create_app_rejects_duplicate_name_in_same_pod(
    authenticated_client,
    test_pod,
):
    pod_id = test_pod["id"]
    app_name = f"duplicate_app_{uuid4().hex[:8]}"

    first = await authenticated_client.post(
        f"/pods/{pod_id}/apps",
        json={"name": app_name},
    )
    assert first.status_code == status.HTTP_201_CREATED, first.text

    second = await authenticated_client.post(
        f"/pods/{pod_id}/apps",
        json={
            "name": app_name,
            "public_slug": f"unique-slug-{uuid4().hex[:8]}",
        },
    )
    assert second.status_code == status.HTTP_409_CONFLICT, second.text
    assert second.json()["code"] == "APP_CONFLICT"


@pytest.mark.asyncio
async def test_delete_app_cleans_up_storage_even_when_archives_share_release_prefix(
    authenticated_client,
    test_pod,
):
    pod_id = test_pod["id"]
    app_name = f"app_delete_{uuid4().hex[:8]}"
    marker = f"DELETE_APP_MARKER_{uuid4().hex[:8]}"

    create_res = await authenticated_client.post(
        f"/pods/{pod_id}/apps",
        json={"name": app_name},
    )
    assert create_res.status_code == status.HTTP_201_CREATED, create_res.text
    app = create_res.json()

    upload_res = await authenticated_client.post(
        f"/pods/{pod_id}/apps/{app_name}/bundle",
        files={
            "source_archive": ("source.zip", build_source_archive(marker), "application/zip"),
            "dist_archive": ("dist.zip", build_dist_archive(marker), "application/zip"),
        },
    )
    assert upload_res.status_code == status.HTTP_200_OK, upload_res.text
    source_archive_path = upload_res.json()["app"]["source_archive_path"]
    assert source_archive_path

    app_storage_root = (
        Path(settings.local_file_storage_root)
        / "common"
        / "apps"
        / app["id"]
    )
    assert app_storage_root.exists()
    # Source archives are content-addressed so a later upload cannot overwrite
    # the version referenced by an in-flight reader.
    assert (app_storage_root / source_archive_path).exists()
    assert any(path.name == "archive.zip" for path in app_storage_root.rglob("archive.zip"))

    delete_res = await authenticated_client.delete(f"/pods/{pod_id}/apps/{app_name}")
    assert delete_res.status_code == status.HTTP_200_OK, delete_res.text
    assert not app_storage_root.exists()

    get_res = await authenticated_client.get(f"/pods/{pod_id}/apps/{app_name}")
    assert get_res.status_code == status.HTTP_404_NOT_FOUND, get_res.text


@pytest.mark.asyncio
async def test_app_source_and_dist_archives_download_round_trip(
    authenticated_client,
    test_pod,
):
    """Regression (DB pool exhaustion): the archive download endpoints resolve in
    a short UoW and stream the stored archive only after the pooled connection is
    released. Both archives must round-trip byte-for-byte end-to-end."""
    pod_id = test_pod["id"]
    app_name = f"app_archive_{uuid4().hex[:8]}"
    marker = f"ARCHIVE_{uuid4().hex[:8]}"

    create_res = await authenticated_client.post(
        f"/pods/{pod_id}/apps",
        json={"name": app_name, "description": "archive round-trip"},
    )
    assert create_res.status_code == status.HTTP_201_CREATED, create_res.text

    source_bytes = build_source_archive(marker)
    dist_bytes = build_dist_archive(marker)
    upload_res = await authenticated_client.post(
        f"/pods/{pod_id}/apps/{app_name}/bundle",
        files={
            "source_archive": ("source.zip", source_bytes, "application/zip"),
            "dist_archive": ("dist.zip", dist_bytes, "application/zip"),
        },
    )
    assert upload_res.status_code == status.HTTP_200_OK, upload_res.text

    source_res = await authenticated_client.get(
        f"/pods/{pod_id}/apps/{app_name}/source/archive"
    )
    assert source_res.status_code == status.HTTP_200_OK, source_res.text
    assert source_res.content == source_bytes

    dist_res = await authenticated_client.get(
        f"/pods/{pod_id}/apps/{app_name}/dist/archive"
    )
    assert dist_res.status_code == status.HTTP_200_OK, dist_res.text
    assert dist_res.content == dist_bytes
