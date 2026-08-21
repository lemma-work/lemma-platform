from __future__ import annotations

import json
import io
import subprocess
import sys
import threading
import time

from lemma_stack import orchestrate
from lemma_stack.config import store
from lemma_stack.release import manifest as release_manifest
from lemma_stack.supervise import Supervisor


def _drive(commands: list[dict], *, settle: float = 5.0) -> list[dict]:
    proc = subprocess.Popen(
        [sys.executable, "-m", "lemma_stack", "supervise", "--dry-run"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    events: list[str] = []

    def reader() -> None:
        for line in proc.stdout:  # type: ignore[union-attr]
            events.append(line.strip())

    threading.Thread(target=reader, daemon=True).start()
    for cmd in commands:
        proc.stdin.write(json.dumps(cmd) + "\n")  # type: ignore[union-attr]
        proc.stdin.flush()  # type: ignore[union-attr]
        time.sleep(settle if cmd.get("cmd") == "start" else 0.3)
    proc.stdin.write(json.dumps({"cmd": "shutdown", "id": "z"}) + "\n")  # type: ignore[union-attr]
    proc.stdin.flush()  # type: ignore[union-attr]
    time.sleep(0.5)
    proc.terminate()
    parsed = []
    for line in events:
        try:
            parsed.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return parsed


def test_supervise_protocol_dry_run():
    events = _drive([{"cmd": "start", "id": "1"}, {"cmd": "status", "id": "2"}])
    by_event = {}
    for ev in events:
        by_event.setdefault(ev["event"], []).append(ev)

    # hello announces protocol 1 + the phase walkthrough
    hello = by_event["hello"][0]
    assert hello["protocol"] == 1
    assert hello["v"] == 1
    assert any(p["key"] == "ready" for p in hello["phases"])

    # a start walks the phases and finishes ready on 3711/8711
    ready = by_event["ready"][0]
    assert ready["url"].endswith(":3711")
    assert ready["api_url"].endswith(":8711")
    done = [e for e in by_event["done"] if e.get("cmd") == "start"][0]
    assert done["ok"] is True

    phase_keys = [e["key"] for e in by_event["phase"]]
    for expected in ("check", "pull", "infra", "migrations", "backend", "frontend", "ready"):
        assert expected in phase_keys

    assert by_event["status"][0]["status"] == "running"


def test_supervise_can_prepare_only_private_infrastructure_for_host_packs():
    events = _drive(
        [{"cmd": "start", "infra_only": True, "id": "infra"}],
        settle=3.0,
    )
    kinds = [event["event"] for event in events]
    phase_keys = [event["key"] for event in events if event["event"] == "phase"]

    assert "infra-ready" in kinds
    assert "ready" not in kinds
    assert "backend" not in phase_keys
    assert "frontend" not in phase_keys
    assert any(
        event["event"] == "done" and event.get("id") == "infra" and event["ok"] for event in events
    )


def test_real_infra_only_start_uses_pin_and_never_runs_app_images_or_migrations(paths, monkeypatch):
    pinned = release_manifest.parse(
        {
            "schema_version": 1,
            "version": "1.2.3",
            "min_admin_version": "0",
            "images": {
                "backend": "backend:test",
                "frontend": "frontend:test",
                "workspace": "workspace:test",
                "function": "function:test",
            },
        }
    )
    release_manifest.pin(paths, pinned)
    config = store.new_document()
    captured = {}
    stopped = []

    supervisor = Supervisor()
    supervisor.paths = paths
    supervisor._out = io.StringIO()
    monkeypatch.setattr(supervisor, "_config", lambda: config)
    monkeypatch.setattr(supervisor, "_resolve_provider", lambda _config: "docker")
    monkeypatch.setattr(
        orchestrate,
        "resolve_manifest",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("pinned native release must not refresh")
        ),
    )
    monkeypatch.setattr(orchestrate, "bring_up", lambda *_args, **kwargs: captured.update(kwargs))
    monkeypatch.setattr(
        orchestrate,
        "bring_down",
        lambda called_paths, called_config, *, infra: stopped.append(
            (called_paths, called_config, infra)
        ),
    )

    supervisor._op_start(setup=False, rebuild=False, infra_only=True)

    assert captured["manifest"].version == "1.2.3"
    assert captured["service_names"] == {"db", "redis", "supertokens"}
    assert captured["pull_infra_only"] is True
    assert captured["migrate"] is False
    assert captured["do_register"] is False
    assert stopped == [(paths, config, False)]
