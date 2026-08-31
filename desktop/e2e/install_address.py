"""Where the Lemma Desktop install on this machine answers, for other tools.

The scenario suite runs against any deployment given a `--base-url`, and a
desktop install is one -- it is the real host pack, the real guest, and the
real services, which is exactly the arrangement nothing else exercises. What
stood between the two was nobody knowing the address: locald allocates its
ports at first launch, so there is no fixed URL to write into a Makefile.

This prints what the install rendered, so the target that runs the suite does
not have to spell a hostname or guess a port. The precedence matches
`app/tests/desktop_e2e/conftest.py` deliberately: a throwaway stack stood up
from the working tree wins over the packaged install, because that is the lane
where a change can be seen working before it becomes a DMG.

Also prints the install's instance id. The suite writes real data to whatever
it is pointed at and cannot delete an organization afterwards, so
`SCENARIOS_TARGET_INSTANCE_ID` is what turns a mistyped host from a silent
write into a stopped run. Reading it here means the caller gets that guard by
default instead of having to know to ask for it.

Usage:
    python3 desktop/e2e/install_address.py          # shell assignments
    python3 desktop/e2e/install_address.py --json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_LOCALD_ROOT = (
    Path.home() / "Library" / "Application Support" / "Lemma" / "locald"
)
CAPABILITIES_PATH = "/health/capabilities"


def locald_root() -> Path:
    override = os.getenv("LEMMA_DESKTOP_E2E_LOCALD_ROOT")
    return Path(override) if override else DEFAULT_LOCALD_ROOT


def _throwaway_api_url(root: Path) -> str | None:
    stack_file = root.parent / "stack.json"
    if not stack_file.is_file():
        return None
    try:
        return str(json.loads(stack_file.read_text())["api_url"]).rstrip("/")
    except (OSError, json.JSONDecodeError, KeyError):
        return None


def _packaged_api_url(root: Path) -> str | None:
    """The API URL out of the rendered pack, never rebuilt from a hostname.

    An install does not necessarily serve `app.lemma.localhost`: the base
    domain is chosen at runtime, because a browser derives no registrable
    domain from `*.localhost` and a pod app framed by the workspace needs one.
    """
    try:
        pack = json.loads((root / "host-pack.json").read_text())
    except (OSError, json.JSONDecodeError):
        return None
    for service in pack.get("services", []):
        env = service.get("env") or {}
        if env.get("API_URL"):
            return str(env["API_URL"]).rstrip("/")
    return None


def api_url() -> str | None:
    root = locald_root()
    return _throwaway_api_url(root) or _packaged_api_url(root)


def instance_id(base_url: str) -> str | None:
    try:
        with urllib.request.urlopen(
            f"{base_url}{CAPABILITIES_PATH}", timeout=10
        ) as response:
            return json.load(response).get("instance_id")
    except (OSError, urllib.error.HTTPError, json.JSONDecodeError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="print JSON instead")
    arguments = parser.parse_args()

    found = api_url()
    if not found:
        print(
            f"no Lemma Desktop install found under {locald_root()}. Start the "
            "app, or set LEMMA_DESKTOP_E2E_LOCALD_ROOT.",
            file=sys.stderr,
        )
        return 1

    identity = instance_id(found)
    if not identity:
        print(
            f"{found} did not answer {CAPABILITIES_PATH}. The install is not "
            "ready yet, or it is not the process that wrote this pack.",
            file=sys.stderr,
        )
        return 1

    if arguments.json:
        print(json.dumps({"api_url": found, "instance_id": identity}))
    else:
        print(f"LEMMA_DESKTOP_API_URL={found}")
        print(f"LEMMA_DESKTOP_INSTANCE_ID={identity}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
