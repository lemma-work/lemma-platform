#!/usr/bin/env python3
"""Every list of "what host this install serves" must name the same domains.

Lemma Desktop serves its workspace under `lemma.localhost`, decided in one
place, and three other places have to agree, in three languages:

* ``desktop/locald/src/local_domain.rs`` decides it. Source of truth.
* ``desktop/src/main.rs`` trusts it for navigation and grants the workspace its
  IPC on its exact origin at runtime -- an allowlist rather than a resolver
  lookup on purpose, since an attacker who controls DNS should not be able to
  talk a security gate into trusting a name.
* ``lemma-python/lemma_sdk/config.py`` believes locald's recorded endpoint, so a
  base it does not know means ``--server local`` stops finding the install.

And one place must *not* name it: ``desktop/capabilities/workspace.json``. A
static capability can only say ``http://app.<base>:*``, and that pattern also
covers the pod-app alias ports the macOS workspace frames apps through --
user-authored code on the workspace's own host. The local workspace is granted
its commands at runtime, on its exact origin, instead.

Nothing tied these together once, and the cost was a shipped build in which
this computer could not pair with its own workspace: the base moved and two
loopback checks still spelled out the old one, so pairing was refused with
nothing logged and onboarding sat on "Connecting this computer" for ever.

This does not ask anyone to keep one list. It asks that the lists say the same
thing, and it fails by naming the file that is behind.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

LOCAL_DOMAIN = ROOT / "desktop/locald/src/local_domain.rs"
SHELL = ROOT / "desktop/src/main.rs"
CAPABILITY = ROOT / "desktop/capabilities/workspace.json"
SDK = ROOT / "lemma-python/lemma_sdk/config.py"


def declared_bases() -> set[str]:
    """The domains `LocalDomain` can serve, from its own constants."""
    text = LOCAL_DOMAIN.read_text(encoding="utf-8")
    found = set(re.findall(r'pub const \w+_BASE: &str = "([^"]+)"', text))
    if not found:
        raise SystemExit(f"no *_BASE constants found in {LOCAL_DOMAIN}")
    return found


def shell_bases() -> set[str]:
    text = SHELL.read_text(encoding="utf-8")
    match = re.search(r"const TRUSTED_LOCAL_BASES: &\[&str\] = &\[([^\]]*)\]", text)
    if not match:
        raise SystemExit(f"TRUSTED_LOCAL_BASES not found in {SHELL}")
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def capability_local_urls() -> list[str]:
    """Local workspace URLs the static capability names. Must be none."""
    urls = json.loads(CAPABILITY.read_text(encoding="utf-8"))["remote"]["urls"]
    return [url for url in urls if re.match(r"https?://app\.", url)]


def sdk_bases() -> set[str]:
    """The domains the SDK treats as a desktop-local server.

    Read out of the file's text rather than imported, and that is not laziness:
    CI runs this script with whatever `python` the runner provides -- see
    `check_script_portability.py` -- while `lemma_sdk/config.py` is 3.14 source
    using PEP 758 `except A, B:`. An old interpreter cannot import it and cannot
    even `ast.parse` it.

    So the coupling is to a private name in another package's source, which is
    as fragile as it sounds. Two things make it survivable: the match spans any
    bracket style and any number of lines, so reformatting does not break it;
    and `test_desktop_local_bases_stay_where_this_script_can_find_them` in
    lemma-python fails on the SDK side if the constant is renamed or moved,
    naming this script.
    """
    if not SDK.exists():
        raise SystemExit(
            "{} is gone. This check reads `_DESKTOP_LOCAL_BASES` out of it; "
            "point SDK at the new home rather than deleting the check.".format(SDK)
        )
    text = SDK.read_text(encoding="utf-8")
    match = re.search(r"_DESKTOP_LOCAL_BASES\s*=\s*[\(\[{]([^)\]}]*)[\)\]}]", text)
    if not match:
        raise SystemExit(
            "`_DESKTOP_LOCAL_BASES` not found in {}. That is this check being "
            "broken, not the domains disagreeing: it reads the constant out of "
            "the SDK's source because CI runs this script on an interpreter too "
            "old to import 3.14 syntax. If the constant moved or was renamed, "
            "update this function.".format(SDK)
        )
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def main() -> int:
    expected = declared_bases()
    consumers = {
        "desktop/src/main.rs (TRUSTED_LOCAL_BASES)": shell_bases(),
        "lemma-python/lemma_sdk/config.py (_DESKTOP_LOCAL_BASES)": sdk_bases(),
    }

    failures = []
    for name, bases in consumers.items():
        missing = expected - bases
        if missing:
            failures.append(
                f"- {name} does not cover {sorted(missing)}; "
                f"it has {sorted(bases)}"
            )
    pinned = capability_local_urls()
    if pinned:
        failures.append(
            f"- desktop/capabilities/workspace.json names {pinned}; the local "
            "workspace is granted at runtime on its exact origin "
            "(local_workspace_capability), because a static pattern would also "
            "cover the pod-app alias ports on the same host"
        )

    if failures:
        print("Local domain lists disagree with local_domain.rs:")
        print("\n".join(failures))
        print(
            "\nEvery base LocalDomain can serve has to appear in both lists. A "
            "base missing from the shell is a workspace that cannot navigate "
            "and is granted no IPC, and from the SDK is `--server local` "
            "failing to find the install."
        )
        return 1

    print(f"Local domain lists agree on {sorted(expected)}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
