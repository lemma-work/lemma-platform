#!/usr/bin/env python3
"""What a release publishes, and whether each registry has it.

One list of packages, one way to resolve the release version, and one way to
ask PyPI and npm for an exact version. The three package workflows and the
release tracker (``report_release_to_slack.py``) all read from here, so "is it
published" means the same thing to the step that decides whether to upload and
to the message that tells people it is out.

Every release before this disagreed with itself somewhere. Each workflow
resolved the version with its own expression, and when the tag push was added
as a trigger the expression resolved to nothing on it (v0.8.0). Each decided
"already published" its own way -- `twine --skip-existing` in two, `npm view`
in the third -- and none checked afterwards that the version had landed.

Usage, from a workflow step::

    python3 scripts/release_registry.py version "$RAW"            # -> version=0.10.0
    python3 scripts/release_registry.py published pypi lemma-sdk 0.10.0
    python3 scripts/release_registry.py wait npm lemma-sdk 0.10.0 --timeout-seconds 600

``version`` and ``published`` also write ``name=value`` to ``$GITHUB_OUTPUT``
when it is set. Standard library only, and runs on the runner's own python3:
the steps that use it run before any toolchain is set up.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional

SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-.][0-9A-Za-z.-]+)?$")

NETWORK_TIMEOUT = 20

#: Registries are CDN-fronted; asking faster than this buys nothing.
POLL_SECONDS = 20

REGISTRY_LABELS = {"pypi": "PyPI", "npm": "npm"}


@dataclass(frozen=True)
class Package:
    """One distribution a release publishes, and the workflow that does it."""

    name: str
    registry: str
    workflow: str

    @property
    def label(self) -> str:
        return "{} ({})".format(self.name, REGISTRY_LABELS[self.registry])

    @property
    def registry_label(self) -> str:
        return REGISTRY_LABELS[self.registry]

    def page(self, version: str) -> str:
        """Where a person sees this exact version on its registry."""
        if self.registry == "pypi":
            return "https://pypi.org/project/{}/{}/".format(self.name, version)
        return "https://www.npmjs.com/package/{}/v/{}".format(self.name, version)


#: Every package a `v*` tag publishes. A package missing from here is a package
#: nobody checks: the tracker would not watch its workflow or ask its registry,
#: and tests/test_release_version_resolution.py would not require its workflow
#: to run on the tag. lemma-stack and lemma-pod-bundle are deliberately absent;
#: neither is published to an index (see docs/versioning.md).
PACKAGES: tuple[Package, ...] = (
    Package("lemma-sdk", "pypi", "release-lemma-python.yml"),
    Package("lemma-terminal", "pypi", "release-lemma-terminal.yml"),
    Package("lemma-sdk", "npm", "release-lemma-typescript.yml"),
)

#: The workflows a release runs besides the package publishes: the one that
#: creates the GitHub Release and the runtime images, and the Desktop build it
#: dispatches on the tag.
RELEASE_WORKFLOWS: tuple[str, ...] = tuple(package.workflow for package in PACKAGES) + (
    "release-local-images.yml",
    "release-desktop.yml",
)


#: Returned for a 404, which is an answer rather than a failure to get one.
ABSENT = object()


def read_json(url: str, token: str = ""):
    """The JSON at ``url``, ``ABSENT`` for a 404, or None when unknown.

    Never raises: a registry being slow or unreachable is "could not check",
    which callers report as such, never as published and never as missing.
    """
    headers = {"Accept": "application/json", "User-Agent": "lemma-release"}
    if token:
        headers["Authorization"] = "Bearer {}".format(token)
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=NETWORK_TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return ABSENT if error.code == 404 else None
    # `http.client.HTTPException` covers a body cut short (IncompleteRead) or a
    # garbled status line, which are neither URLError nor OSError -- and one
    # escaping would end a tracker that polls for hours before its summary.
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, ValueError, OSError):
        return None


def pypi_has(package: str, version: str) -> Optional[bool]:
    """Whether ``pip install package==version`` can find a file. None when unknown.

    The exact-version endpoint, and it must list at least one file: a version
    whose files were all deleted, or whose upload never completed, is a name
    PyPI knows and cannot install.
    """
    data = read_json("https://pypi.org/pypi/{}/{}/json".format(package, version))
    if data is ABSENT:
        return False
    if data is None:
        return None
    return bool(data.get("urls"))


def npm_has(package: str, version: str) -> Optional[bool]:
    """Whether npm serves exactly ``version`` of ``package``. None when unknown."""
    data = read_json("https://registry.npmjs.org/{}/{}".format(package, version))
    if data is ABSENT:
        return False
    if data is None:
        return None
    return data.get("version") == version


CHECKS: dict[str, Callable[[str, str], Optional[bool]]] = {
    "pypi": pypi_has,
    "npm": npm_has,
}


def is_published(registry: str, package: str, version: str) -> Optional[bool]:
    return CHECKS[registry](package, version)


def normalize_version(raw: str) -> str:
    """``v0.10.0`` or ``0.10.0`` to ``0.10.0``; ValueError for anything else.

    An empty string is an error, not "no version": every v0.8.0 tag push
    resolved its version to nothing, and a gate that read empty as "unset"
    passed the release it was meant to check.
    """
    version = (raw or "").strip()
    version = version[1:] if version[:1] in ("v", "V") else version
    if not SEMVER.fullmatch(version):
        raise ValueError("'{}' is not a release version".format(raw))
    return version


def _write_output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("{}={}\n".format(name, value))
    print("{}={}".format(name, value))


def _command_version(arguments: argparse.Namespace) -> int:
    try:
        version = normalize_version(arguments.raw)
    except ValueError as error:
        print("::error title=Not a release version::{}".format(error))
        return 1
    _write_output("version", version)
    return 0


def _command_published(arguments: argparse.Namespace) -> int:
    """Answer yes or no, or fail: an unknown answer is never guessed.

    Guessing "no" would upload over a published version (npm refuses, loudly,
    which is fine) and guessing "yes" would skip a publish that never happened
    and report success -- the one outcome this exists to rule out.
    """
    for attempt in range(arguments.attempts):
        state = is_published(arguments.registry, arguments.package, arguments.version)
        if state is not None:
            _write_output("published", "true" if state else "false")
            if state:
                print(
                    "::notice title=Already published::{} {} is already on {}; "
                    "nothing to publish.".format(
                        arguments.package,
                        arguments.version,
                        REGISTRY_LABELS[arguments.registry],
                    )
                )
            return 0
        if attempt + 1 < arguments.attempts:
            time.sleep(POLL_SECONDS)
    print(
        "::error title=Registry unreachable::Could not ask {} whether {} {} is "
        "published. Re-run the workflow.".format(
            REGISTRY_LABELS[arguments.registry], arguments.package, arguments.version
        )
    )
    return 1


def _command_wait(arguments: argparse.Namespace) -> int:
    deadline = time.monotonic() + max(arguments.timeout_seconds, 0)
    label = REGISTRY_LABELS[arguments.registry]
    while True:
        state = is_published(arguments.registry, arguments.package, arguments.version)
        if state:
            print("{} {} is on {}".format(arguments.package, arguments.version, label))
            return 0
        if time.monotonic() >= deadline:
            print(
                "::error title={} {} is not on {}::{} did not serve {} {} within "
                "{} seconds.{}".format(
                    arguments.package,
                    arguments.version,
                    label,
                    label,
                    arguments.package,
                    arguments.version,
                    arguments.timeout_seconds,
                    " " + arguments.hint if arguments.hint else "",
                )
            )
            return 1
        print(
            "{} {} not on {} yet ({}); retrying".format(
                arguments.package,
                arguments.version,
                label,
                "could not check" if state is None else "absent",
            )
        )
        time.sleep(POLL_SECONDS)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)

    version = commands.add_parser(
        "version", help="Normalize and validate a release version."
    )
    version.add_argument("raw", help="A tag or version, for example v0.10.0")
    version.set_defaults(handler=_command_version)

    for name, handler in (("published", _command_published), ("wait", _command_wait)):
        command = commands.add_parser(name)
        command.add_argument("registry", choices=sorted(CHECKS))
        command.add_argument("package")
        command.add_argument("version")
        command.set_defaults(handler=handler)
        if name == "published":
            command.add_argument("--attempts", type=int, default=3)
        else:
            command.add_argument("--timeout-seconds", type=int, default=600)
            command.add_argument("--hint", default="", help="Appended to the error.")

    arguments = parser.parse_args(argv)
    return arguments.handler(arguments)


if __name__ == "__main__":
    sys.exit(main())
