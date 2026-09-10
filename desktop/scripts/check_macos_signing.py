"""Verify stable identities on a packaged app and across an installed upgrade."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import plistlib
import subprocess


HELPERS = {
    "Contents/MacOS/lemma-locald": "work.lemma.locald",
    "Contents/MacOS/lemma-agent-host": "work.lemma.agent-host",
    "Contents/MacOS/lemma-runtime": "work.lemma.runtime",
    "Contents/Resources/lemma-vz": "work.lemma.vz",
}


@dataclass(frozen=True)
class Signature:
    identifier: str
    team: str
    authorities: tuple[str, ...]
    adhoc: bool


def parse_signature(output: str) -> Signature:
    fields: dict[str, list[str]] = {}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            fields.setdefault(key, []).append(value)
    return Signature(
        identifier=fields.get("Identifier", [""])[0],
        team=fields.get("TeamIdentifier", [""])[0],
        authorities=tuple(fields.get("Authority", [])),
        adhoc="adhoc" in fields.get("Signature", []),
    )


def validate_signature(
    signature: Signature, *, identifier: str, team: str, allow_development: bool
) -> None:
    if signature.identifier != identifier:
        raise ValueError(
            f"identifier changed: expected {identifier}, got {signature.identifier}"
        )
    if signature.adhoc or signature.team in {"", "not set"}:
        raise ValueError("ad-hoc signing cannot preserve trust across changed binaries")
    if signature.team != team:
        raise ValueError(f"signing team changed: expected {team}, got {signature.team}")
    prefixes = ("Developer ID Application:",)
    if allow_development:
        prefixes += ("Apple Development:",)
    if not signature.authorities or not signature.authorities[0].startswith(prefixes):
        raise ValueError("expected a Developer ID Application signing identity")


def codesign(*arguments: str) -> str:
    result = subprocess.run(
        ["/usr/bin/codesign", *arguments], capture_output=True, text=True, timeout=30
    )
    if result.returncode:
        raise ValueError(f"codesign failed: {result.stderr.strip()}")
    return result.stdout + result.stderr


def designated_requirement(output: str) -> str:
    for line in output.splitlines():
        if line.startswith("designated => "):
            return line.removeprefix("designated => ")
    raise ValueError("signature has no designated requirement")


# Without this key macOS never asks. It does not deny loudly either: the app
# simply cannot reach anything on the local network, which for Lemma means its
# own loopback services -- the backend, the frontend, the auth service. A build
# missing it is broken on first launch and looks like a hung startup.
LOCAL_NETWORK_USAGE_KEY = "NSLocalNetworkUsageDescription"


def validate_bundle_metadata(app: Path) -> None:
    """The Info.plist keys a shipped bundle cannot work without.

    Checked here rather than in a workflow because both DMG pipelines call this
    file and only one of them was checking it. The nightly asked for this key;
    the release, which is the one that reaches users, did not.
    """
    info = app / "Contents/Info.plist"
    try:
        with info.open("rb") as handle:
            payload = plistlib.load(handle)
    except (OSError, plistlib.InvalidFileException) as error:
        raise ValueError(f"{info} could not be read: {error}") from error
    description = payload.get(LOCAL_NETWORK_USAGE_KEY)
    if not isinstance(description, str) or not description.strip():
        raise ValueError(
            f"{app.name} has no {LOCAL_NETWORK_USAGE_KEY}. macOS will not ask "
            f"for local network access, and the app cannot reach its own "
            f"loopback services -- which looks like a startup that never "
            f"finishes rather than a permission that was never requested"
        )
    print(f"verified {LOCAL_NETWORK_USAGE_KEY} is present")  # noqa: T201 -- CLI report


def check(
    app: Path,
    *,
    team: str,
    bundle_id: str = "work.lemma.desktop",
    previous_app: Path | None = None,
    allow_development: bool = False,
) -> None:
    codesign("--verify", "--deep", "--strict", str(app))
    for relative, identifier in {".": bundle_id, **HELPERS}.items():
        binary = app / relative
        validate_signature(
            parse_signature(codesign("-dv", "--verbose=4", str(binary))),
            identifier=identifier,
            team=team,
            allow_development=allow_development,
        )
        codesign("--verify", "--strict", str(binary))
        if previous_app is not None:
            previous = previous_app / relative
            codesign("--verify", "--strict", str(previous))
            validate_signature(
                parse_signature(codesign("-dv", "--verbose=4", str(previous))),
                identifier=identifier,
                team=team,
                allow_development=allow_development,
            )
            requirement = designated_requirement(codesign("-dr", "-", str(previous)))
            # Ask macOS to evaluate the previous identity against new code;
            # comparing printed requirements would mishandle equivalent rules.
            codesign(
                "--verify",
                "--strict",
                "--test-requirement",
                "=" + requirement,
                str(binary),
            )
        print(f"verified signing continuity: {identifier}")  # noqa: T201 -- CLI report
    validate_bundle_metadata(app)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path)
    parser.add_argument("--team-id", required=True)
    parser.add_argument("--bundle-id", default="work.lemma.desktop")
    parser.add_argument("--previous-app", type=Path)
    parser.add_argument(
        "--allow-development",
        action="store_true",
        help="QA only; never for release promotion",
    )
    args = parser.parse_args()
    check(
        args.app,
        team=args.team_id,
        bundle_id=args.bundle_id,
        previous_app=args.previous_app,
        allow_development=args.allow_development,
    )


if __name__ == "__main__":
    main()
