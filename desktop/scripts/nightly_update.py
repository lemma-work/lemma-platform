"""Stage versioned nightly updates and assemble one complete platform feed."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil

TARGETS = {
    "darwin-aarch64": ("aarch64-apple-darwin", "macos-aarch64", "aarch64.app.tar.gz"),
    "windows-x86_64": ("x86_64-pc-windows-msvc", "windows-x86_64", "x64-setup.exe"),
}
VERSION = re.compile(
    r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)-nightly\.([1-9]\d*)(?:\.([1-9]\d*))?"
)


def version_order(version: str) -> tuple[int, ...]:
    match = VERSION.fullmatch(version)
    if not match:
        raise ValueError(f"Not a nightly version: {version}")
    return tuple(int(part or 0) for part in match.groups())


def nightly_version(base: str, run: int, attempt: int) -> str:
    version = f"{base}-nightly.{run}.{attempt}"
    version_order(version)
    if attempt < 1:
        raise ValueError("Run attempt must be positive")
    return version


def read_object(path: Path) -> dict[str, object]:
    # Release artifacts are untyped JSON until their required fields are checked.
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"Expected an object in {path}")
    return value


def object_field(value: dict[str, object], name: str) -> dict[str, object]:
    field = value.get(name)
    if not isinstance(field, dict):
        raise ValueError(f"Missing object: {name}")
    return field


def artifact_size(manifest: dict[str, object], kind: str, target: str) -> int:
    artifact = object_field(object_field(manifest, kind), target)
    size = artifact.get("size")
    if type(size) is not int or size <= 0:
        raise ValueError(f"Invalid runtime size: {target}")
    return size


def stage(
    target: str,
    version: str,
    manifest_path: Path,
    payload: Path,
    directory: Path,
    repository: str,
) -> None:
    version_order(version)
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repository):
        raise ValueError("Invalid release repository")
    host, guest, suffix = TARGETS[target]
    manifest = read_object(manifest_path)
    if version.split("-nightly.")[0] != manifest.get("version"):
        raise ValueError("App and runtime versions disagree")
    signature = Path(str(payload) + ".sig").read_text().strip()
    if not signature or payload.stat().st_size == 0:
        raise ValueError("Missing signed update payload")
    size = artifact_size(manifest, "host_packs", host) + artifact_size(
        manifest, "guest_runtimes", guest
    )
    postgres = object_field(manifest, "infra").get("postgres")
    major_match = (
        re.search(r"-pg(\d+)(?:[.@:]|$)", postgres)
        if isinstance(postgres, str)
        else None
    )
    if not major_match:
        raise ValueError("Missing PostgreSQL compatibility version")
    name = f"Lemma_{version}_{suffix}"
    directory.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(payload, directory / name)
    (directory / (name + ".sig")).write_text(signature + "\n")
    fragment = {
        "version": version,
        "platform": target,
        "entry": {
            "url": f"https://github.com/{repository}/releases/download/desktop-nightly/{name}",
            "signature": signature,
        },
        "lemma": {
            "postgres_major": int(major_match[1]),
            "runtime_download_bytes": size,
        },
        "sha256": hashlib.sha256(payload.read_bytes()).hexdigest(),
    }
    (directory / f"{target}.json").write_text(json.dumps(fragment, indent=2) + "\n")


def assemble(
    directory: Path, repository: str, current: Path | None = None
) -> dict[str, object]:
    fragments = {
        target: read_object(directory / f"{target}.json") for target in TARGETS
    }
    versions = [fragment.get("version") for fragment in fragments.values()]
    version = versions[0]
    if not isinstance(version, str) or any(item != version for item in versions):
        raise ValueError("Platform builds must have the same nightly version")
    order = version_order(version)
    platforms: dict[str, object] = {}
    metadata: dict[str, object] = {}
    for target, fragment in fragments.items():
        if fragment.get("platform") != target:
            raise ValueError("Platform artifact is mislabeled")
        entry = object_field(fragment, "entry")
        suffix = TARGETS[target][2]
        name = f"Lemma_{version}_{suffix}"
        signature = (directory / (name + ".sig")).read_text().strip()
        if (
            not signature
            or entry.get("signature") != signature
            or (directory / name).stat().st_size == 0
        ):
            raise ValueError("Feed signature does not match the staged payload")
        expected_url = (
            f"https://github.com/{repository}/releases/download/desktop-nightly/{name}"
        )
        if entry.get("url") != expected_url:
            raise ValueError("Feed URL does not name its immutable payload")
        if (
            fragment.get("sha256")
            != hashlib.sha256((directory / name).read_bytes()).hexdigest()
        ):
            raise ValueError("Payload changed after it was staged")
        platforms[target] = entry
        platform_metadata = object_field(fragment, "lemma")
        for field in ("postgres_major", "runtime_download_bytes"):
            value = platform_metadata.get(field)
            if type(value) is not int or value <= 0:
                raise ValueError(f"Invalid platform metadata: {target}.{field}")
        metadata[target] = platform_metadata
    # Older macOS nightlies consume the top-level block; newer clients select
    # their own platform's size and compatibility information.
    legacy = object_field(fragments["darwin-aarch64"], "lemma")
    result = {
        "version": version,
        "pub_date": datetime.now(timezone.utc).isoformat(),
        "notes": f"Lemma Desktop nightly {version}",
        "platforms": platforms,
        "lemma": {**legacy, "platforms": metadata},
    }
    if current is not None:
        previous = read_object(current)
        previous_version = previous.get("version")
        if (
            not isinstance(previous_version, str)
            or version_order(previous_version) > order
        ):
            raise ValueError("Refusing to replace a newer nightly feed")
        if version_order(previous_version) == order:
            if any(previous.get(key) != result[key] for key in ("platforms", "lemma")):
                raise ValueError(
                    "Refusing to replace an existing nightly with different artifacts"
                )
            return previous
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    version = commands.add_parser("version")
    version.add_argument("--manifest", type=Path, required=True)
    version.add_argument("--cargo", type=Path, required=True)
    version.add_argument("--run", type=int, required=True)
    version.add_argument("--attempt", type=int, required=True)
    version.add_argument("--output", type=Path, required=True)
    version.add_argument("--overlay", type=Path, required=True)
    prepare = commands.add_parser("stage")
    prepare.add_argument("--target", choices=TARGETS, required=True)
    prepare.add_argument("--version", required=True)
    prepare.add_argument("--manifest", type=Path, required=True)
    prepare.add_argument("--payload", type=Path, required=True)
    prepare.add_argument("--directory", type=Path, required=True)
    prepare.add_argument("--repository", required=True)
    feed = commands.add_parser("feed")
    feed.add_argument("--directory", type=Path, required=True)
    feed.add_argument("--current", type=Path)
    feed.add_argument("--output", type=Path, required=True)
    feed.add_argument("--repository", required=True)
    args = parser.parse_args()
    if args.command == "version":
        base = read_object(args.manifest).get("version")
        section = (
            args.cargo.read_text().split("[workspace.package]", 1)[1].split("\n[", 1)[0]
        )
        crate = re.search(r'^version\s*=\s*"([^"]+)"', section, re.M)
        if not isinstance(base, str) or not crate or crate[1] != base:
            raise ValueError("Runtime and Cargo versions disagree")
        value = nightly_version(base, args.run, args.attempt)
        args.overlay.write_text(json.dumps({"version": value}) + "\n")
        with args.output.open("a") as output:
            output.write(f"version={value}\nruntime_version={base}\n")
    elif args.command == "stage":
        stage(
            args.target,
            args.version,
            args.manifest,
            args.payload,
            args.directory,
            args.repository,
        )
    else:
        args.output.write_text(
            json.dumps(
                assemble(args.directory, args.repository, args.current), indent=2
            )
            + "\n"
        )


if __name__ == "__main__":
    main()
