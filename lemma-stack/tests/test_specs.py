from __future__ import annotations

import pytest

from lemma_stack.config import store
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
                "agentbox_workspace": "ghcr.io/lemma-work/lemma-agentbox-workspace:v1.0.0",
                "agentbox_function": "ghcr.io/lemma-work/lemma-agentbox-function:v1.0.0",
            },
        }
    )


@pytest.fixture
def config(paths):
    return store.load_or_create(paths)


def build(config, paths, manifest, provider="podman", *, host_apps=False):
    socket = "/run/user/501/podman/podman.sock" if provider == "podman" else "/var/run/docker.sock"
    return specs_mod.build_specs(
        config,
        paths,
        manifest,
        provider=provider,
        host_socket=socket,
        host_apps=host_apps,
    )


def by_name(specs, name):
    return next(s for s in specs if s.name == name)


def test_start_order_and_host_ports(config, paths, manifest):
    specs = build(config, paths, manifest)
    names = [s.name for s in specs]
    assert names == ["db", "redis", "supertokens", "backend", "frontend"]
    published = {s.name: s.ports for s in specs}
    # only the two app-facing services publish host ports
    assert published["db"] == ()
    assert published["redis"] == ()
    assert published["supertokens"] == ()
    assert published["backend"] == ((8711, 8000),)
    assert published["frontend"] == ((3711, 8080),)


def test_kreuzberg_legacy_flag_cannot_add_a_local_service(config, paths, manifest):
    store.set_value(config, "features.kreuzberg", "true")
    specs = build(config, paths, manifest)
    assert "kreuzberg" not in [s.name for s in specs]
    backend = by_name(specs, "backend")
    assert backend.env["KREUZBERG_URL"] == ""
    assert backend.env["DOCUMENT_PROCESSOR"] == "markitdown"


def test_document_processor_is_in_process_markitdown(config, paths, manifest):
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


def test_host_pack_infra_forwards_are_loopback_only(config, paths, manifest):
    specs = build(config, paths, manifest, host_apps=True)
    published = {spec.name: spec.ports for spec in specs}

    assert published["db"] == ((55432, 5432),)
    assert published["redis"] == ((56379, 6379),)
    assert published["supertokens"] == ((53567, 3567),)
    for service in ("db", "redis", "supertokens"):
        args = run_args(by_name(specs, service), selinux=False)
        port_values = [args[index + 1] for index, value in enumerate(args) if value == "-p"]
        assert port_values and all(value.startswith("127.0.0.1:") for value in port_values)


def test_selinux_adds_z_to_rw_binds_only(config, paths, manifest):
    spec = by_name(build(config, paths, manifest, provider="podman"), "backend")
    args = run_args(spec, selinux=True)
    mounts = [args[i + 1] for i, a in enumerate(args) if a == "-v"]
    state_mount = next(v for v in mounts if "/app/.local/lemma" in v)
    socket_mount = next(v for v in mounts if "podman.sock" in v)
    assert state_mount.endswith(":z")
    assert not socket_mount.endswith(":z")  # sockets must not be relabeled


def test_embedded_agentbox_podman_wiring(config, paths, manifest):
    spec = by_name(build(config, paths, manifest, provider="podman"), "backend")
    assert spec.env["AGENTBOX_PROVIDER"] == "docker"
    assert spec.env["AGENTBOX_API_URL"] == "http://backend:8000/internal/agentbox"
    assert spec.env["AGENTBOX_DOCKER_SOCKET_PATH"] == "/var/run/docker.sock"
    assert spec.env["AGENTBOX_DOCKER_PRIVATE_NETWORK"] == "lemma-local-net"
    assert spec.env["AGENTBOX_STATE_DATABASE_URL"].startswith(
        "postgresql+asyncpg://"
    )
    assert spec.env["AGENTBOX_STATE_DATABASE_URL"].endswith("/agentbox")
    assert len(spec.env["FUNCTION_RUNTIME_SECRET"]) >= 32
    assert spec.env["FUNCTION_RUNTIME_SECRET"] != spec.env["AGENTBOX_RUNTIME_CREDENTIAL_KEY"]
    assert len(spec.env["AGENTBOX_RUNTIME_CREDENTIAL_KEY"]) == 44
    assert spec.env["AGENTBOX_ADD_HOST_GATEWAY"] == "false"
    assert spec.env["AGENTBOX_LOCAL_RUNTIME_TIMEOUT_SECONDS"] == "600"
    assert spec.user == "root"
    mounts = dict((t, s) for s, t, _ in spec.binds)
    assert mounts["/var/run/docker.sock"] == "/run/user/501/podman/podman.sock"


def test_embedded_agentbox_docker_wiring(config, paths, manifest):
    spec = by_name(build(config, paths, manifest, provider="docker"), "backend")
    assert spec.env["AGENTBOX_PROVIDER"] == "docker"
    # Sandboxes and the backend share this private network. lemma-cli inside a
    # sandbox reaches the all-in-one API through the backend DNS alias.
    assert spec.env["AGENTBOX_DOCKER_SOCKET_PATH"] == "/var/run/docker.sock"
    assert spec.env["AGENTBOX_DOCKER_PRIVATE_NETWORK"] == "lemma-local-net"
    assert spec.env["WORKSPACE_CALLBACK_API_URL"] == "http://backend:8000"
    assert spec.env["FUNCTION_RUNTIME_GATEWAY_URL"] == "http://backend:8000"
    assert spec.env["WORKSPACE_CALLBACK_AUTH_URL"] == "http://frontend:8080/auth"
    assert spec.env["WORKSPACE_CALLBACK_FRONTEND_URL"] == "http://frontend:8080"
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

    spec = by_name(build(config, paths, manifest, provider=provider), "backend")

    assert spec.security_opts == expected


def test_agentbox_security_opts_change_config_hash(config, paths, manifest, monkeypatch):
    monkeypatch.setattr(specs_mod.platform, "system", lambda: "Linux")
    monkeypatch.setattr(specs_mod, "selinux_enforcing", lambda: False)
    unconfined = by_name(build(config, paths, manifest, provider="podman"), "backend")
    monkeypatch.setattr(specs_mod.platform, "system", lambda: "Darwin")
    guarded = by_name(build(config, paths, manifest, provider="podman"), "backend")

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
    assert env["AGENTBOX_API_URL"] == "http://backend:8000/internal/agentbox"
    assert env["WORKSPACE_CALLBACK_API_URL"] == "http://backend:8000"
    assert env["FUNCTION_RUNTIME_GATEWAY_URL"] == "http://backend:8000"
    assert env["SCHEDULER_API_URL"] == "http://backend:8000"
    assert env["API_URL"] == "http://app.lemma.localhost:8711"
    assert env["FRONTEND_URL"] == "http://app.lemma.localhost:3711"
    assert env["AUTH_FRONTEND_URL"] == "http://app.lemma.localhost:3711/auth"
    assert env["APP_BASE_DOMAIN"] == "apps.lemma.localhost:8711"
    assert env["SESSION_COOKIE_DOMAIN"] == ""
    assert env["STORAGE_BACKEND"] == "local"
    assert env["LOCAL_KREUZBERG_ENABLED"] == "false"
    assert env["EMBEDDING_PROVIDER"] == "local"
    assert env["AUTH_EMAIL_VERIFICATION_REQUIRED"] == "false"
    assert env["LOCAL_HTTP_ACCESS_LOGS_ENABLED"] == "true"
    assert env["AUTH_EMAIL_DELIVERABILITY_CHECKS_ENABLED"] == "false"
    assert env["AUTH_DISPOSABLE_EMAIL_DOMAINS_ENABLED"] == "false"
    assert env["AUTH_ABUSE_PROTECTION_ENABLED"] == "false"
    assert env["AUTH_ALTCHA_ENABLED"] == "false"
    assert env["DESKTOP_AUTH_CREATE_LIMIT"] == "0"
    assert env["AGENTBOX_API_KEY"] == store.agentbox_api_key(config)
    assert env["AGENTBOX_PUBLIC_URL"] == (
        "http://app.lemma.localhost:8711/internal/agentbox"
    )
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
    assert backend.env["API_URL"] == "http://app.lemma.localhost:9000"
    assert frontend.env["NEXT_PUBLIC_API_URL"] == "http://app.lemma.localhost:9000"
    assert frontend.env["NEXT_PUBLIC_SITE_URL"] == "http://app.lemma.localhost:4000"
    assert frontend.env["NEXT_PUBLIC_AUTH_EMAIL_VERIFICATION_REQUIRED"] == "false"
    assert backend.env["APP_BASE_DOMAIN"] == "apps.lemma.localhost:9000"


def test_backend_runs_the_all_in_one_local_entrypoint(config, paths, manifest):
    backend = by_name(build(config, paths, manifest), "backend")

    assert backend.command[:2] == ("uvicorn", "local_app:app")
    assert backend.wait_http == (
        "http://app.lemma.localhost:8711/internal/agentbox/health/ready"
    )
    assert "agentbox" not in [spec.name for spec in build(config, paths, manifest)]
