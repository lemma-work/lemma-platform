"""Exercise the shipped publisher with a local release store, never GitHub."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[5]


def mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    assert all(isinstance(key, str) for key in value)
    return dict(value)


def publication_job() -> dict[str, object]:
    # YAML is an untyped configuration boundary; validate the shapes we consume.
    # BaseLoader preserves GitHub's `on` key instead of YAML 1.1's boolean True.
    document: object = yaml.load(
        (ROOT / ".github/workflows/release-local-images.yml").read_text(),
        Loader=yaml.BaseLoader,
    )
    return mapping(mapping(mapping(document)["jobs"])["publish-nightly-update"])


def publisher() -> str:
    steps = publication_job()["steps"]
    assert isinstance(steps, list)
    scripts = [mapping(step).get("run") for step in steps]
    return next(script for script in scripts if isinstance(script, str))


@pytest.fixture
def release_store(tmp_path: Path) -> Path:
    script = tmp_path / "desktop/scripts/nightly_update.py"
    script.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "desktop/scripts/nightly_update.py", script)
    manifest = tmp_path / "runtime.json"
    manifest.write_text(
        json.dumps(
            {
                "version": "0.7.2",
                "host_packs": {
                    "aarch64-apple-darwin": {"size": 100},
                    "x86_64-pc-windows-msvc": {"size": 101},
                },
                "guest_runtimes": {
                    "macos-aarch64": {"size": 200},
                    "windows-x86_64": {"size": 201},
                },
                "infra": {"postgres": "postgres-pg18@sha256:fixture"},
            }
        )
    )
    payload = tmp_path / "payload"
    payload.write_bytes(b"fixture installer")
    payload.with_suffix(".sig").write_text("fixture signature")
    for target in ("darwin-aarch64", "windows-x86_64"):
        subprocess.run(
            [
                sys.executable,
                str(script),
                "stage",
                "--target",
                target,
                "--version",
                "0.7.2-nightly.10.1",
                "--manifest",
                str(manifest),
                "--payload",
                str(payload),
                "--directory",
                str(tmp_path / "nightly-update"),
                "--repository",
                "lemma-work/lemma-platform",
            ],
            check=True,
            capture_output=True,
            timeout=10,
        )
    remote = tmp_path / "remote"
    remote.mkdir()
    (remote / "latest.json").write_text(json.dumps({"version": "0.7.2-nightly.9.1"}))
    (remote / "old-nightly-runtime.zip").write_bytes(b"keep installed releases usable")
    stub = tmp_path / "gh"
    stub.write_text(
        f"#!{sys.executable}\n"
        """
import json, os, pathlib, shutil, sys
args = sys.argv[1:]
root = pathlib.Path(os.environ["TEST_RELEASE_ROOT"])
remote = root / "remote"
with (root / "calls.jsonl").open("a") as log:
    log.write(json.dumps(args) + "\\n")
if args[:2] == ["release", "view"]:
    print(json.dumps({"assets": [{"name": path.name} for path in remote.iterdir()]}))
elif args[:2] == ["release", "download"]:
    if os.environ.get("TEST_FAIL_DOWNLOAD"): sys.exit(1)
    destination = pathlib.Path(args[args.index("--dir") + 1])
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(remote / "latest.json", destination / "latest.json")
elif args[:2] == ["release", "upload"]:
    for raw in args[3:]:
        if raw.startswith("--"): continue
        source = pathlib.Path(raw)
        if os.environ.get("TEST_FAIL_PAYLOAD") and source.name != "latest.json": sys.exit(1)
        if os.environ.get("TEST_FAIL_FEED") and source.name == "latest.json": sys.exit(1)
        shutil.copyfile(source, remote / source.name)
else:
    raise SystemExit("Unexpected mutation of the release store")
"""
    )
    stub.chmod(0o755)
    (tmp_path / "publish.sh").write_text(publisher())
    return tmp_path


def publish(root: Path, **overrides: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(root / "publish.sh")],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=20,
        env={
            **os.environ,
            "PATH": f"{root}:{os.environ['PATH']}",
            "GITHUB_REPOSITORY": "lemma-work/lemma-platform",
            "GITHUB_SHA": "fixture-revision",
            "RUNNER_TEMP": str(root / "runner"),
            "GITHUB_STEP_SUMMARY": str(root / "summary"),
            "TEST_RELEASE_ROOT": str(root),
            **overrides,
        },
    )


def calls(root: Path) -> list[list[str]]:
    return [
        json.loads(line) for line in (root / "calls.jsonl").read_text().splitlines()
    ]


def test_feed_is_published_after_both_payloads_and_old_runtime_assets_survive(
    release_store: Path,
) -> None:
    (release_store / "runner").mkdir()
    result = publish(release_store)
    assert result.returncode == 0, result.stderr
    uploads = [
        call for call in calls(release_store) if call[:2] == ["release", "upload"]
    ]
    assert len(uploads) == 2
    assert any(name.endswith(".exe") for name in uploads[0])
    assert any(name.endswith(".app.tar.gz") for name in uploads[0])
    assert "nightly-update/latest.json" in uploads[1]
    feed = json.loads((release_store / "remote/latest.json").read_text())
    assert feed["version"] == "0.7.2-nightly.10.1"
    assert set(feed["platforms"]) == {"darwin-aarch64", "windows-x86_64"}
    assert (
        release_store / "remote/old-nightly-runtime.zip"
    ).read_bytes() == b"keep installed releases usable"


@pytest.mark.parametrize(
    "failure", ["TEST_FAIL_DOWNLOAD", "TEST_FAIL_PAYLOAD", "TEST_FAIL_FEED"]
)
def test_failure_preserves_the_working_feed_and_never_claims_publication(
    release_store: Path, failure: str
) -> None:
    (release_store / "runner").mkdir()
    original = (release_store / "remote/latest.json").read_bytes()
    result = publish(release_store, **{failure: "1"})
    assert result.returncode != 0
    assert (release_store / "remote/latest.json").read_bytes() == original
    assert (release_store / "remote/old-nightly-runtime.zip").exists()
    assert not (release_store / "summary").exists()


@pytest.mark.parametrize("invalid", ["older", "missing-platform"])
def test_stale_or_incomplete_builds_cannot_mutate_published_assets(
    release_store: Path, invalid: str
) -> None:
    (release_store / "runner").mkdir()
    if invalid == "older":
        (release_store / "remote/latest.json").write_text(
            json.dumps({"version": "0.7.2-nightly.11.1"})
        )
    else:
        (release_store / "nightly-update/windows-x86_64.json").unlink()
    result = publish(release_store)
    assert result.returncode != 0
    assert all(call[:2] != ["release", "upload"] for call in calls(release_store))


def test_retry_of_an_identical_feed_is_a_noop(release_store: Path) -> None:
    (release_store / "runner").mkdir()
    assert publish(release_store).returncode == 0
    previous = (release_store / "remote/latest.json").read_bytes()
    (release_store / "calls.jsonl").unlink()
    result = publish(release_store)
    assert result.returncode == 0, result.stderr
    assert all(call[:2] != ["release", "upload"] for call in calls(release_store))
    assert (release_store / "remote/latest.json").read_bytes() == previous


def test_joint_publication_waits_for_both_platform_builds() -> None:
    needs = publication_job()["needs"]
    assert isinstance(needs, list)
    assert set(needs) == {"share-desktop-dmg", "share-desktop-exe"}
