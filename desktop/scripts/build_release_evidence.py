#!/usr/bin/env python3
"""What was published, what it hashes to, and who signed it.

A release attaches a DMG, an update payload, its signature and a feed, and
records none of it. So there is no way, afterwards, to answer the questions that
matter when something is wrong: is the file on this laptop the file we
published, which release does a given DMG belong to, and was the identity that
signed it the identity we expect?

None of these are answerable from the release page. GitHub shows sizes, and the
notarization ticket proves Apple saw *a* build, not which one. A user reporting
a bug cannot tell you what they installed, and neither can we.

So the release carries a manifest: every attached file with its SHA-256, the
signing identity read back off the app bundle, the commit, the runtime versions
it will fetch on first launch, and the key that signs its updates. It is written
next to the artifacts and attached with them, and `--verify` re-reads it against
the files on disk, which is what the release workflow runs before uploading.

Deliberately not an SBOM. An SBOM of the app's own dependencies is a separate
job with separate tooling; this is the smaller thing nobody had, and the one a
person actually reaches for.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

MANIFEST_NAME = "release-evidence.json"

# The manifest describes the other files, so it never describes itself.
EXCLUDED = {MANIFEST_NAME}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def signing_identity(app: Path | None) -> str | None:
    """The Developer ID that signed the bundle, read back off the bundle.

    Read rather than declared: what a workflow *meant* to sign with is already
    in the workflow, and the question this answers is what it actually did.
    """
    if app is None or not app.exists():
        return None
    result = subprocess.run(
        ["/usr/bin/codesign", "-dvvv", str(app)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    for line in (result.stdout + result.stderr).splitlines():
        if line.startswith("Authority="):
            return line.removeprefix("Authority=").strip()
    return None


def updater_key_id(config: Path) -> str | None:
    """The key installed apps verify this release's updates with.

    Read from the comment line minisign writes into the key file, which is where
    minisign itself prints the id. Recording it is the point: a release whose
    updates no installed app can verify looks exactly like one whose updates
    they can, and afterwards nothing says which key it was.
    """
    try:
        pubkey = json.loads(config.read_text())["plugins"]["updater"]["pubkey"]
    except (OSError, KeyError, json.JSONDecodeError):
        return None
    if not pubkey:
        return None
    try:
        text = base64.b64decode(pubkey, validate=True).decode()
    except (binascii.Error, UnicodeDecodeError):
        return None
    for line in text.splitlines():
        if "minisign public key:" in line:
            return line.rsplit(":", 1)[1].strip()
    return None


def runtime_versions(manifest: Path) -> dict[str, object]:
    """What this build downloads on first launch, and how much of it."""
    try:
        payload = json.loads(manifest.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    sizes = {
        name: entry.get("size")
        for group in ("host_packs", "guest_runtimes")
        for name, entry in payload.get(group, {}).items()
    }
    return {"version": payload.get("version"), "sizes": sizes}


def artifacts(directory: Path) -> list[dict[str, object]]:
    return [
        {"name": path.name, "size": path.stat().st_size, "sha256": sha256(path)}
        for path in sorted(directory.iterdir())
        if path.is_file() and path.name not in EXCLUDED
    ]


def build(
    directory: Path,
    *,
    version: str,
    commit: str,
    repository: str,
    app: Path | None,
    config: Path,
    runtime_manifest: Path,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "version": version,
        "commit": commit,
        "repository": repository,
        "signing_identity": signing_identity(app),
        "updater_key_id": updater_key_id(config),
        "runtime": runtime_versions(runtime_manifest),
        "artifacts": artifacts(directory),
    }


def verify(
    directory: Path,
    manifest: dict[str, object],
    *,
    expected: dict[str, object] | None = None,
) -> list[str]:
    """Re-read the manifest against the files beside it, and against its inputs.

    Hashes alone are not enough. They prove the files are the files the manifest
    describes; they say nothing about whether the manifest describes *this*
    release. A version, commit or repository that is simply wrong -- a rerun
    against a stale checkout, a copied step, a hand-edited file -- passed a
    hash-only check while claiming to be a release it is not, which is the one
    question this file exists to answer.

    `expected` is the same values the build was given. Recomputed rather than
    trusted, so what is compared is what this run actually is.
    """
    problems: list[str] = []
    for field, value in (expected or {}).items():
        if manifest.get(field) != value:
            problems.append(
                f"the manifest records {field}={manifest.get(field)!r} and this "
                f"release is {value!r}"
            )
    recorded = {entry["name"]: entry for entry in manifest["artifacts"]}
    present = {path.name for path in directory.iterdir() if path.is_file()} - EXCLUDED
    for missing in sorted(recorded.keys() - present):
        problems.append(f"{missing} is in the manifest and not on disk")
    for extra in sorted(present - recorded.keys()):
        problems.append(f"{extra} is on disk and not in the manifest")
    for name in sorted(recorded.keys() & present):
        actual = sha256(directory / name)
        if actual != recorded[name]["sha256"]:
            problems.append(
                f"{name} hashes to {actual}, and the manifest records "
                f"{recorded[name]['sha256']}"
            )
    # `or ""`, not a default: the key is present and null when codesign could
    # not be read, which is the case this most needs to report rather than
    # crash on.
    identity = manifest.get("signing_identity") or ""
    if not identity.startswith("Developer ID Application:"):
        problems.append(
            f"the app was signed by {manifest.get('signing_identity')!r}, which is "
            f"not a Developer ID Application identity"
        )
    if not re.fullmatch(r"[0-9A-F]{16}", manifest.get("updater_key_id") or ""):
        problems.append(
            "the manifest records no updater key id, so nothing here says which "
            "key installed apps will verify this release's updates with"
        )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--version", default="")
    parser.add_argument("--commit", default="")
    parser.add_argument("--repository", default="")
    parser.add_argument("--app", type=Path)
    parser.add_argument("--config", type=Path, default=Path("desktop/tauri.conf.json"))
    parser.add_argument(
        "--runtime-manifest", type=Path, default=Path("desktop/runtime/lemma-local.json")
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="re-read an existing manifest against the files beside it",
    )
    arguments = parser.parse_args()
    target = arguments.directory / MANIFEST_NAME

    if arguments.verify:
        # The same inputs the build was given, so the check is against this
        # release rather than against the manifest's own claims.
        expected = {
            field: value
            for field, value in (
                ("version", arguments.version),
                ("commit", arguments.commit),
                ("repository", arguments.repository),
            )
            if value
        }
        expected["updater_key_id"] = updater_key_id(arguments.config)
        expected["runtime"] = runtime_versions(arguments.runtime_manifest)
        problems = verify(
            arguments.directory, json.loads(target.read_text()), expected=expected
        )
        for problem in problems:
            print(f"::error::release evidence: {problem}", file=sys.stderr)
        if problems:
            return 1
        print(f"✓ release evidence verified against {arguments.directory}")
        return 0

    manifest = build(
        arguments.directory,
        version=arguments.version,
        commit=arguments.commit,
        repository=arguments.repository,
        app=arguments.app,
        config=arguments.config,
        runtime_manifest=arguments.runtime_manifest,
    )
    target.write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
