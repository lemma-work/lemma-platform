from __future__ import annotations

import pytest

from lemma_stack.config import render, store
from lemma_stack.release import manifest as m
from lemma_stack.stack import specs as specs_mod
from lemma_stack.stack.specs import run_args


@pytest.fixture
def manifest():
    return m.parse(
        {
            "schema_version": 1,
            "version": "1.0.0",
            "min_admin_version": "0.1.0",
            "images": {
                "backend": "ghcr.io/lemma-work/lemma-backend:v1.0.0",
                "frontend": "ghcr.io/lemma-work/lemma-frontend:v1.0.0",
                "agentbox": "ghcr.io/lemma-work/lemma-agentbox:v1.0.0",
                "agentbox_runtime": "ghcr.io/lemma-work/lemma-agentbox-runtime:v1.0.0",
            },
        }
    )


@pytest.fixture
def config(paths):
    return store.load_or_create(paths)


def build(config, paths, manifest, provider="podman"):
    socket = "/run/user/501/podman/podman.sock" if provider == "podman" else "/var/run/docker.sock"
    return specs_mod.build_specs(
        config, paths, manifest, provider=provider, host_socket=socket
    )


def by_name(specs, name):
    return next(s for s in specs if s.name == name)


def test_start_order_and_host_ports(config, paths, manifest):
    specs = build(config, paths, manifest)
    names = [s.name for s in specs]
    assert names == ["db", "redis", "supertokens", "agentbox", "backend", "frontend"]
    published = {s.name: s.ports for s in specs}
    # only the three app-facing services publish host ports
    assert published["db"] == ()
    assert published["redis"] == ()
    assert published["supertokens"] == ()
    assert published["agentbox"] == ((8721, 8000),)
    assert published["backend"] == ((8711, 8000),)
    assert published["frontend"] == ((3711, 8080),)


def test_kreuzberg_included_when_enabled(config, paths, manifest):
    store.set_value(config, "features.kreuzberg", "true")
    specs = build(config, paths, manifest)
    assert "kreuzberg" in [s.name for s in specs]
    backend = by_name(specs, "backend")
    assert backend.env["KREUZBERG_URL"] == "http://kreuzberg:8000"
    assert backend.env["DOCUMENT_PROCESSOR"] == "kreuzberg"


def test_document_processor_defaults_to_markitdown_without_kreuzberg(config, paths, manifest):
    # Default install (kreuzberg disabled): no container, and the backend uses
    # the in-process markitdown adapter with no Kreuzberg URL.
    specs = build(config, paths, manifest)
    assert "kreuzberg" not in [s.name for s in specs]
    backend = by_name(specs, "backend")
    assert backend.env["DOCUMENT_PROCESSOR"] == "markitdown"
    assert backend.env["KREUZBERG_URL"] == ""


def test_run_args_snapshot_db(config, paths, manifest):
    spec = by_name(build(config, paths, manifest), "db")
    args = run_args(spec, selinux=False)
    joined = " ".join(args)
    assert args[0:2] == ["run", "-d"]
    assert "--name lemma-local-db" in joined
    assert "--network lemma-local-net" in joined
    assert "--network-alias db" in joined
    assert "--restart unless-stopped" in joined
    assert "lemma-local-postgres-data:/var/lib/postgresql/data" in joined
    assert f"{paths.postgres_init_dir}:/docker-entrypoint-initdb.d:ro" in joined
    assert "--health-cmd pg_isready -U postgres -h localhost" in joined
    assert "-p" not in args  # no host ports for infra
    assert args[-1] == "docker.io/pgvector/pgvector:0.8.3-pg16"


def test_run_args_loopback_only_ports(config, paths, manifest):
    spec = by_name(build(config, paths, manifest), "backend")
    args = run_args(spec, selinux=False)
    port_values = [args[i + 1] for i, a in enumerate(args) if a == "-p"]
    assert port_values == ["127.0.0.1:8711:8000"]


def test_selinux_adds_z_to_rw_binds_only(config, paths, manifest):
    spec = by_name(build(config, paths, manifest, provider="podman"), "agentbox")
    args = run_args(spec, selinux=True)
    mounts = [args[i + 1] for i, a in enumerate(args) if a == "-v"]
    state_mount = next(v for v in mounts if "/app/.local/lemma" in v)
    socket_mount = next(v for v in mounts if "podman.sock" in v)
    assert state_mount.endswith(":z")
    assert not socket_mount.endswith(":z")  # sockets must not be relabeled


def test_agentbox_podman_wiring(config, paths, manifest):
    spec = by_name(build(config, paths, manifest, provider="podman"), "agentbox")
    assert spec.env["AGENTBOX_PROVIDER"] == "podman"
    assert spec.env["CONTAINER_HOST"] == "unix:///run/podman/podman.sock"
    assert spec.env["AGENTBOX_NETWORK"] == "lemma-local-net"
    assert spec.env["AGENTBOX_ADD_HOST_GATEWAY"] == "false"
    assert spec.user == "root"
    mounts = dict((t, s) for s, t, _ in spec.binds)
    assert mounts["/run/podman/podman.sock"] == "/run/user/501/podman/podman.sock"


def test_agentbox_docker_wiring(config, paths, manifest):
    spec = by_name(build(config, paths, manifest, provider="docker"), "agentbox")
    assert spec.env["AGENTBOX_PROVIDER"] == "docker"
    assert "CONTAINER_HOST" not in spec.env
    mounts = dict((t, s) for s, t, _ in spec.binds)
    assert mounts["/var/run/docker.sock"] == "/var/run/docker.sock"


@pytest.mark.parametrize(
    ("host_platform", "host_selinux", "provider", "expected"),
    [
        ("Linux", True, "podman", ("label=disable",)),
        ("Linux", True, "docker", ("label=disable",)),
        ("Linux", False, "podman", ()),
        ("Darwin", False, "podman", ("label=disable",)),
        ("Windows", False, "podman", ("label=disable",)),
        ("Darwin", False, "docker", ()),
        ("Windows", False, "docker", ()),
    ],
)
def test_agentbox_socket_selinux_guard_matrix(
    monkeypatch,
    config,
    paths,
    manifest,
    host_platform,
    host_selinux,
    provider,
    expected,
):
    monkeypatch.setattr(specs_mod.platform, "system", lambda: host_platform)
    monkeypatch.setattr(specs_mod, "selinux_enforcing", lambda: host_selinux)

    spec = by_name(build(config, paths, manifest, provider=provider), "agentbox")

    assert spec.security_opts == expected


def test_agentbox_security_opts_change_config_hash(config, paths, manifest, monkeypatch):
    monkeypatch.setattr(specs_mod.platform, "system", lambda: "Linux")
    monkeypatch.setattr(specs_mod, "selinux_enforcing", lambda: False)
    unconfined = by_name(build(config, paths, manifest, provider="podman"), "agentbox")
    monkeypatch.setattr(specs_mod.platform, "system", lambda: "Darwin")
    guarded = by_name(build(config, paths, manifest, provider="podman"), "agentbox")

    assert unconfined.security_opts == ()
    assert guarded.security_opts == ("label=disable",)
    assert unconfined.config_hash() != guarded.config_hash()


def test_config_hash_changes_with_image_and_env(config, paths, manifest):
    spec = by_name(build(config, paths, manifest), "backend")
    base_hash = spec.config_hash()
    store.set_value(config, "LEMMA_OPENAI_API_KEY", "sk-1")
    changed = by_name(build(config, paths, manifest), "backend")
    assert changed.config_hash() != base_hash
    # unchanged config -> stable hash
    again = by_name(build(config, paths, manifest), "backend")
    assert again.config_hash() == changed.config_hash()


def test_backend_env_golden(config, paths, manifest):
    env = by_name(build(config, paths, manifest), "backend").env
    assert env["DATABASE_URL"] == "postgresql+asyncpg://postgres:postgres@db:5432/lemma"
    assert env["REDIS_URL"] == "redis://redis:6379"
    assert env["SUPERTOKENS_CORE_URL"] == "http://supertokens:3567"
    assert env["AGENTBOX_API_URL"] == "http://agentbox:8000"
    assert env["WORKSPACE_CALLBACK_API_URL"] == "http://backend:8000"
    assert env["SCHEDULER_API_URL"] == "http://backend:8000"
    assert env["API_URL"] == "http://127-0-0-1.sslip.io:8711"
    assert env["FRONTEND_URL"] == "http://127-0-0-1.sslip.io:3711"
    assert env["AUTH_FRONTEND_URL"] == "http://127-0-0-1.sslip.io:3711/auth"
    assert env["APP_BASE_DOMAIN"] == "127-0-0-1.sslip.io:8711"
    assert env["SESSION_COOKIE_DOMAIN"] == ".127-0-0-1.sslip.io"
    assert env["STORAGE_BACKEND"] == "local"
    assert env["LOCAL_KREUZBERG_ENABLED"] == "false"
    assert env["EMBEDDING_PROVIDER"] == "local"
    assert env["AUTH_EMAIL_VERIFICATION_REQUIRED"] == "false"
    assert env["AGENTBOX_API_KEY"] == store.agentbox_api_key(config)
    # chat surfaces default to no-public-URL receive modes
    assert env["ENABLE_TELEGRAM_POLLING_MODE"] == "true"
    assert env["ENABLE_SLACK_SOCKET_MODE"] == "true"
    assert "host.docker.internal" not in " ".join(env.values())


def test_custom_ports_flow_into_urls(config, paths, manifest):
    store.set_value(config, "ports.frontend", "4000")
    store.set_value(config, "ports.backend", "9000")
    specs = build(config, paths, manifest)
    backend = by_name(specs, "backend")
    frontend = by_name(specs, "frontend")
    assert backend.ports == ((9000, 8000),)
    assert backend.env["API_URL"] == "http://127-0-0-1.sslip.io:9000"
    assert frontend.env["NEXT_PUBLIC_API_URL"] == "http://127-0-0-1.sslip.io:9000"
    assert frontend.env["NEXT_PUBLIC_SITE_URL"] == "http://127-0-0-1.sslip.io:4000"
    assert frontend.env["NEXT_PUBLIC_AUTH_EMAIL_VERIFICATION_REQUIRED"] == "false"
    assert backend.env["APP_BASE_DOMAIN"] == "127-0-0-1.sslip.io:9000"
    assert render.agentbox_app_domain(config) == "127-0-0-1.sslip.io:8721"
