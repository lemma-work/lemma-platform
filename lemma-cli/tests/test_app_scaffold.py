"""Unit tests for HTML app scaffolding and source classification."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import lemma_cli.cli_core.app_scaffold as app_scaffold
from lemma_cli.cli_app.app_bundle import classify_app_source
from lemma_cli.cli_core.app_scaffold import (
    BUNDLED_TEMPLATE_SOURCE,
    DEFAULT_TEMPLATE_SOURCE,
    AppScaffoldOptions,
    _load_html_starter_template,
    scaffold_app,
    scaffold_html_app,
)


def _options(target: Path, **overrides) -> AppScaffoldOptions:
    base = {
        "target_dir": target,
        "name": "my-app",
        "pod_id": "pod_123",
        "api_url": "http://127.0.0.1:8710",
        "auth_url": "http://127.0.0.1:3710/auth",
        "title": "My App",
        "navigation": "sidebar",
        "agent_name": None,
        "chat_mode": "right-sidebar",
        "members": False,
        "search_config": None,
        "theme_toggle": True,
        "install": False,
        "registry": False,
        "style_preset": "soft",
        "template_source": BUNDLED_TEMPLATE_SOURCE,
        "sdk_path": None,
    }
    base.update(overrides)
    return AppScaffoldOptions(**base)


def test_html_starter_template_loads_from_package():
    # Guards the packaging move: the template must be shipped + importable.
    template = _load_html_starter_template()
    assert "<!doctype html>" in template
    assert "__LEMMA_APP_TITLE__" in template
    assert "/public/sdk/lemma-client.js" in template
    assert "window.LemmaClient.LemmaClient" in template


def test_scaffold_html_app_writes_index(tmp_path):
    target = tmp_path / "myapp"
    steps = scaffold_html_app(target, title="My <Cool> App")

    index = target / "index.html"
    assert index.exists()
    html = index.read_text(encoding="utf-8")
    assert "__LEMMA_APP_TITLE__" not in html  # sentinel substituted
    assert "My &lt;Cool&gt; App" in html  # title escaped + substituted
    assert "window.__LEMMA_CONFIG__" in html
    assert any("wrote" in step for step in steps)


def test_scaffold_html_app_rejects_non_empty_directory(tmp_path):
    target = tmp_path / "myapp"
    target.mkdir()
    (target / "x.txt").write_text("hi", encoding="utf-8")
    with pytest.raises(ValueError):
        scaffold_html_app(target, title="X")


def test_classify_app_source_tiers(tmp_path):
    html_file = tmp_path / "page.html"
    html_file.write_text("<h1>hi</h1>", encoding="utf-8")
    assert classify_app_source(html_file) == "html"

    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<h1>hi</h1>", encoding="utf-8")
    assert classify_app_source(static_dir) == "static"

    vite_dir = tmp_path / "vite"
    vite_dir.mkdir()
    (vite_dir / "package.json").write_text("{}", encoding="utf-8")
    assert classify_app_source(vite_dir) == "vite"


def test_classify_app_source_rejects_unknown(tmp_path):
    not_html = tmp_path / "data.txt"
    not_html.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError):
        classify_app_source(not_html)

    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    with pytest.raises(ValueError):
        classify_app_source(empty_dir)


def test_default_template_is_offline_bundled():
    # The default must not be a remote git URL.
    assert DEFAULT_TEMPLATE_SOURCE == BUNDLED_TEMPLATE_SOURCE
    assert "://" not in DEFAULT_TEMPLATE_SOURCE
    assert "github" not in DEFAULT_TEMPLATE_SOURCE.lower()


def test_scaffold_app_offline_no_network(tmp_path, monkeypatch):
    # Hard-fail if the scaffold tries to shell out (git clone, npm install).
    def _boom(*args, **kwargs):
        raise AssertionError(f"unexpected subprocess: {args!r}")

    monkeypatch.setattr(app_scaffold.subprocess, "run", _boom)

    target = tmp_path / "app"
    steps = scaffold_app(_options(target))

    assert any("bundled offline starter" in step for step in steps)
    assert (target / "package.json").exists()
    assert (target / "src" / "main.tsx").exists()


def test_scaffold_app_tsconfig_resolution(tmp_path):
    target = tmp_path / "app"
    scaffold_app(_options(target))

    tsconfig = json.loads((target / "tsconfig.json").read_text(encoding="utf-8"))
    compiler = tsconfig["compilerOptions"]
    assert compiler["moduleResolution"] == "Bundler"
    assert compiler["types"] == ["vite/client"]
    assert (target / "src" / "vite-env.d.ts").exists()


def test_scaffold_app_package_depends_on_lemma_sdk(tmp_path):
    target = tmp_path / "app"
    scaffold_app(_options(target))

    package = json.loads((target / "package.json").read_text(encoding="utf-8"))
    assert package["name"] == "my-app"
    assert "lemma-sdk" in package["dependencies"]


def test_scaffold_app_sdk_path_override(tmp_path):
    fake_sdk = tmp_path / "local-sdk"
    fake_sdk.mkdir()
    (fake_sdk / "package.json").write_text('{"name": "lemma-sdk"}', encoding="utf-8")

    target = tmp_path / "app"
    scaffold_app(_options(target, sdk_path=str(fake_sdk)))

    package = json.loads((target / "package.json").read_text(encoding="utf-8"))
    assert package["dependencies"]["lemma-sdk"] == f"file:{fake_sdk.resolve()}"


def test_scaffold_app_writes_env_local(tmp_path):
    target = tmp_path / "app"
    scaffold_app(_options(target))

    env_local = (target / ".env.local").read_text(encoding="utf-8")
    assert 'VITE_LEMMA_API_URL="http://127.0.0.1:8710"' in env_local
    assert 'VITE_LEMMA_AUTH_URL="http://127.0.0.1:3710/auth"' in env_local
    assert 'VITE_LEMMA_POD_ID="pod_123"' in env_local
    # No --proxy → no dev-only env override file.
    assert not (target / ".env.development.local").exists()


def test_scaffold_app_client_reads_runtime_config(tmp_path):
    # Portable apps: the client bakes no pod context — it reads the host-injected
    # window.__LEMMA_CONFIG__ at runtime (VITE_* stays a dev-only fallback).
    target = tmp_path / "app"
    scaffold_app(_options(target))

    client = (target / "src" / "lemma-client.ts").read_text(encoding="utf-8")
    assert "new LemmaClient()" in client
    assert "import.meta.env.VITE_LEMMA_POD_ID" not in client
    # .env.local is still written so `vite dev` has a fallback.
    assert (target / ".env.local").exists()


def test_scaffold_app_proxy_keeps_real_api_url_in_env_local(tmp_path):
    # The proxy '/api' override must live in .env.development.local (dev only) so
    # `vite build` (deploy) still bakes the REAL API URL — otherwise a deployed
    # proxy-mode app calls its own origin and fails to authenticate.
    target = tmp_path / "app"
    scaffold_app(_options(target, proxy=True))

    env_local = (target / ".env.local").read_text(encoding="utf-8")
    assert 'VITE_LEMMA_API_URL="http://127.0.0.1:8710"' in env_local

    dev_local = (target / ".env.development.local").read_text(encoding="utf-8")
    assert 'VITE_LEMMA_API_URL="/api"' in dev_local
    assert 'LEMMA_DEV_PROXY_TARGET="http://127.0.0.1:8710"' in dev_local


def test_scaffold_app_writes_manifest_at_importer_path(tmp_path):
    target = tmp_path / "app"
    scaffold_app(_options(target))

    manifest_path = target / "apps" / "my-app" / "my-app.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["name"] == "my-app"
    assert manifest["podId"] == "pod_123"
    # The lemma.app.json alias is also written for deploy tooling.
    assert (target / "lemma.app.json").exists()


def test_scaffold_app_title_substituted(tmp_path):
    target = tmp_path / "app"
    scaffold_app(_options(target, title="Sales App"))

    index = (target / "index.html").read_text(encoding="utf-8")
    main = (target / "src" / "main.tsx").read_text(encoding="utf-8")
    assert "__LEMMA_APP_TITLE__" not in index
    assert "__LEMMA_APP_TITLE__" not in main
    assert "Sales App" in index
    assert "Sales App" in main


def test_scaffold_app_gitignore_renamed(tmp_path):
    target = tmp_path / "app"
    scaffold_app(_options(target))

    assert (target / ".gitignore").exists()
    assert not (target / "gitignore").exists()


# --- lemma-sdk dependency resolution at `app init` (latest-at-init, no static pin) ---


def test_resolve_sdk_spec_pins_latest_published_version(monkeypatch):
    monkeypatch.setattr(app_scaffold, "resolve_local_sdk_path", lambda: None)
    monkeypatch.setattr(
        app_scaffold, "_latest_published_lemma_sdk_version", lambda: "1.2.3"
    )
    assert app_scaffold._resolve_lemma_sdk_spec(None) == "^1.2.3"


def test_resolve_sdk_spec_falls_back_to_template_latest_when_offline(monkeypatch):
    monkeypatch.setattr(app_scaffold, "resolve_local_sdk_path", lambda: None)
    monkeypatch.setattr(
        app_scaffold, "_latest_published_lemma_sdk_version", lambda: None
    )
    # None => leave the template's "latest" untouched (npm install still gets newest).
    assert app_scaffold._resolve_lemma_sdk_spec(None) is None


def test_resolve_sdk_spec_uses_sdk_path_file_link(tmp_path: Path):
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    assert (
        app_scaffold._resolve_lemma_sdk_spec(str(tmp_path))
        == f"file:{tmp_path.resolve()}"
    )


def test_update_package_json_writes_resolved_latest(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(app_scaffold, "resolve_local_sdk_path", lambda: None)
    monkeypatch.setattr(
        app_scaffold, "_latest_published_lemma_sdk_version", lambda: "9.9.9"
    )
    pkg = tmp_path / "package.json"
    pkg.write_text(
        json.dumps({"name": "tmpl", "dependencies": {"lemma-sdk": "latest"}}),
        encoding="utf-8",
    )
    app_scaffold._update_package_json(pkg, _options(tmp_path))
    data = json.loads(pkg.read_text(encoding="utf-8"))
    assert data["dependencies"]["lemma-sdk"] == "^9.9.9"
