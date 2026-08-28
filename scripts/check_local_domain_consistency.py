#!/usr/bin/env python3
"""Every list of "what host this install serves" must name the same domains.

Lemma Desktop does not serve one fixed hostname. `LocalDomain` picks a base
domain at runtime -- `*.localhost` when nothing else resolves, a loopback
wildcard otherwise -- because a browser derives no registrable domain from
`*.localhost`, so a pod app framed by the workspace can hold no session there.

Four places have to agree about the result, in three languages:

* ``desktop/locald/src/local_domain.rs`` decides it. Source of truth.
* ``desktop/src/main.rs`` trusts it for navigation -- an allowlist rather than a
  resolver lookup on purpose, since an attacker who controls DNS should not be
  able to talk a security gate into trusting a name.
* ``desktop/capabilities/workspace.json`` grants the workspace its IPC, and a
  workspace served on an origin missing from it reaches no shell command at all.
* ``lemma-python/lemma_sdk/config.py`` believes locald's recorded endpoint, so a
  base it does not know means ``--server local`` stops finding the install.

Nothing tied them together, and the cost of that was a shipped build in which
this computer could not pair with its own workspace: the base moved to
``127.0.0.1.sslip.io`` and two loopback checks still spelled out
``.localhost``, so pairing was refused with nothing logged and onboarding sat
on "Connecting this computer" for ever.

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


def capability_bases() -> set[str]:
    urls = json.loads(CAPABILITY.read_text(encoding="utf-8"))["remote"]["urls"]
    bases = set()
    for url in urls:
        match = re.match(r"https?://app\.([^:/]+)", url)
        if match:
            bases.add(match.group(1))
    return bases


def sdk_bases() -> set[str]:
    text = SDK.read_text(encoding="utf-8")
    match = re.search(r"_DESKTOP_LOCAL_BASES = \(([^)]*)\)", text)
    if not match:
        raise SystemExit(f"_DESKTOP_LOCAL_BASES not found in {SDK}")
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def main() -> int:
    expected = declared_bases()
    consumers = {
        "desktop/src/main.rs (TRUSTED_LOCAL_BASES)": shell_bases(),
        "desktop/capabilities/workspace.json (remote.urls)": capability_bases(),
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

    if failures:
        print("Local domain lists disagree with local_domain.rs:")
        print("\n".join(failures))
        print(
            "\nEvery base LocalDomain can serve has to appear in all of them. A "
            "base missing from the shell is a workspace that cannot navigate, "
            "from the capability is a workspace with no IPC at all, and from the "
            "SDK is `--server local` failing to find the install."
        )
        return 1

    print(f"Local domain lists agree on {sorted(expected)}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
