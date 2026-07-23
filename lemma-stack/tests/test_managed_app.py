from __future__ import annotations

from pathlib import Path

from lemma_stack import app as stack_app


class FakeManagedClient:
    root = Path("/managed/locald")
    binary = Path("/Applications/Lemma.app/Contents/MacOS/lemma-locald")

    def __init__(self) -> None:
        self.requests: list[tuple[str, dict]] = []

    def request(self, command: str, **payload):
        self.requests.append((command, payload))
        if command == "control.snapshot":
            return {
                "event": "control.snapshot",
                "managed_runtime": {"engine": "containerd"},
                "services": [
                    {"id": "backend", "running": True, "circuit_open": False},
                    {"id": "frontend", "running": True, "circuit_open": False},
                ],
                "operator": {
                    "readiness": {"ai": "ready"},
                    "config": {
                        "schema_version": 1,
                        "install_id": "a" * 32,
                        "revision": 2,
                        "onboarding_complete": False,
                        "ai": {
                            "protocol": "unconfigured",
                            "base_url": "",
                            "default_model": "",
                            "models": [],
                            "vision_models": [],
                            "allow_private_network": False,
                            "last_validated_at_unix_ms": None,
                        },
                        "integrations": {"composio_enabled": False},
                        "surfaces": {"slack_socket_mode": False},
                    },
                    "secrets": {"ai.api_key": False},
                },
            }
        if command == "status":
            return {
                "event": "status",
                "release": "1.2.3",
                "status": "running",
                "ready": True,
                "url": "http://app.lemma.localhost:3711",
                "components": [
                    {
                        "id": "backend",
                        "running": True,
                        "pid": 42,
                        "circuit_open": False,
                        "restart_count": 0,
                    }
                ],
                "managed_runtime": {
                    "engine": "containerd",
                    "endpoint_host": "127.0.0.1",
                },
            }
        return {"event": "done", "ok": True}


def test_lifecycle_commands_route_through_managed_daemon(monkeypatch) -> None:
    client = FakeManagedClient()
    monkeypatch.setattr(stack_app, "_managed_locald", lambda: client)
    monkeypatch.setattr(
        stack_app,
        "_load_context",
        lambda: (_ for _ in ()).throw(AssertionError("legacy path used")),
    )

    stack_app.start()
    stack_app.stop(infra=True)
    stack_app.restart()

    assert client.requests == [
        ("start", {}),
        ("status", {}),
        ("stop", {"infra": True}),
        ("restart", {}),
        ("status", {}),
    ]


def test_managed_status_and_doctor_do_not_probe_external_runtimes(monkeypatch, capsys) -> None:
    client = FakeManagedClient()
    monkeypatch.setattr(stack_app, "_managed_locald", lambda: client)
    monkeypatch.setattr(
        stack_app.detect,
        "detect",
        lambda: (_ for _ in ()).throw(AssertionError("external runtime probed")),
    )

    stack_app.status(json_output=True)
    stack_app.doctor(json_output=True)

    output = capsys.readouterr().out
    assert '"provider": "managed-local"' in output
    assert '"ai-provider"' in output
    assert client.requests == [("status", {}), ("control.snapshot", {})]


def test_managed_config_uses_transactional_daemon_api_and_write_only_secrets(
    monkeypatch, capsys
) -> None:
    client = FakeManagedClient()
    monkeypatch.setattr(stack_app, "_managed_locald", lambda: client)

    stack_app.config_set(
        [
            "ai.protocol=openai_compat",
            "ai.base_url=http://127.0.0.1:11434/v1",
            "ai.api_key=local-secret",
        ]
    )
    stack_app.config_get("ai.api_key")

    apply = client.requests[1]
    assert apply[0] == "config.apply"
    assert apply[1]["payload"]["config"]["ai"]["protocol"] == "openai_compat"
    assert apply[1]["payload"]["secrets"] == {"ai.api_key": "local-secret"}
    assert "local-secret" not in capsys.readouterr().out
