#!/usr/bin/env python3
"""Keep the words for the old workspace sandbox from coming back.

The per-user "workspace sandbox" became a **machine**: `/machines` replaced the
`/workspace/*` routes and `/workspace-ports` (which remain only as deprecated
shims on top of it), the generated `workspace*` SDK clients went, and a
machine's paths come from one module, `lemma-backend/sandbox_runtime/paths.py`
(home `/home/user`, projects `~/lemma`). A rename like that is never finished
by the change that makes it. The old names live on in prompts, skills and
examples, and an agent that reads "cd /workspace/repos" acts on it: it writes
to a directory that does not exist and reports the failure as the user's
problem. A caller taught a shim breaks when the shim is retired.

Four rules, each narrow enough to be close to false-positive-free:

- **`/workspace` as a filesystem path.** A rooted `/workspace` -- `cd
  /workspace`, `cwd="/workspace"`, `/workspace/repos`. Not
  `app/modules/workspace`, which is a module, and not `~/workspace`.
- **Deprecated route names**, which still answer as shims on top of
  `/machines` but must not be taught or newly called: `/workspace/files`,
  `/workspace/status`, `/workspace/browser`, `/workspace/apps`, `/workspace-ports`, and the
  removed operation ids (`workspace.files.list`, `workspace.browser.status`,
  `workspace.status` and the rest). Log event names such as
  `workspace.files.read_failed.degraded` are the module's and pass, as do
  tests (which exercise the shims) and a line that says the name is deprecated.
- **Old SDK names**: `WorkspaceService`, `WorkspaceAppsService`, and, in docs
  and skills, `lemma.workspace.` -- the TypeScript SDK keeps that one as a
  deprecated alias, so a line that says so passes.
- **"Sandbox" where the agent means its machine**, in agent-facing text only
  (the agent prompts, `lemma-skills/`, `agent_tool_schemas.json`): "your
  sandbox", "the sandbox", "this sandbox", "workspace sandbox", "your
  workspace", "workspace container". The function sandbox is a different
  thing and keeps its name.

A legitimate use -- a historical release note, a migration, the shims' code --
goes in `stale-machine-terms-allow.txt` beside this file, one
`path-or-glob[:rule]  # reason` per line.

Stdlib only, and no syntax newer than 3.9: `make quality` runs it with whatever
`python3` is on the `PATH`.
"""

from __future__ import annotations

import fnmatch
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
ALLOW_FILE = Path(__file__).with_name("stale-machine-terms-allow.txt")
SELF = "scripts/check_stale_machine_terms.py"
#: The gate's own test spells every stale term on purpose.
SELF_TEST = "tests/test_check_stale_machine_terms.py"
ALLOW_PATH = "scripts/stale-machine-terms-allow.txt"

#: Generated clients, specs and inventories carry whatever their source said.
#: Flagging them reports one fault twice and points at a file nobody may edit.
SKIP_PARTS = (
    "/node_modules/",
    "/.venv/",
    "/openapi_client/",
    "/.generated/",
    "/generated/",
    "/build/",
    "/dist/",
    "/vendor/",
    "/openapi_specs/",
)
SKIP_SUFFIXES = (
    ".lock",
    "lock.json",
    "lock.yaml",
    "openapi_spec.json",
    "openapi.json",
    "lemma-client.js",
    "lemma-ui.js",
    ".min.js",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".pdf",
    ".woff",
    ".woff2",
    ".ttf",
)
#: Produced by scripts from the routes and contracts; regenerate, never edit.
SKIP_PATHS = (
    "lemma-backend/docs/modules/route-inventory.md",
    "lemma-backend/docs/contracts/*",
    "lemma-backend/app/core/log/event_catalog.py",
)
MAX_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: "re.Pattern[str]"
    message: str
    #: Globs the rule applies to; empty means every scanned file.
    scope: Tuple[str, ...] = ()
    #: For names that still exist as deprecated shims: a line that says
    #: "deprecated" is documenting the shim, and a test exercises it.
    shim: bool = False

    def applies_to(self, path: str) -> bool:
        if self.shim and is_test(path):
            return False
        return not self.scope or any(fnmatch.fnmatch(path, glob) for glob in self.scope)


def is_test(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return (
        "/tests/" in f"/{path}"
        or name.startswith("test_")
        or name.endswith(("_test.py", ".test.ts", ".test.tsx", ".spec.ts"))
    )


AGENT_FACING = (
    "lemma-backend/app/modules/agent/prompts/*.md",
    "lemma-skills/*",
    "lemma-backend/agent_tool_schemas.json",
)
DOCS_AND_SKILLS = AGENT_FACING + ("docs/*", "*.md")

#: A rooted `/workspace`: not preceded by a path or word character (so
#: `modules/workspace` and `~/workspace` pass) and not continued by one (so
#: `/workspaces` and `/workspace-ports` are not this rule's business).
_ROOTED = r"(?<![\w.~/-])/workspace(?![\w-])"

OLD_ROUTE = Rule(
    "old-route",
    re.compile(
        r"(?<![\w.~-])/workspace/(?:files|status|browser|apps)\b"
        r"|(?<![\w.~-])/workspace/\*"
        r"|/workspace-ports\b"
        r"|\bworkspace\.files\.(?:list|stat|content)\b"
        r"|\bworkspace\.browser\.(?:access|status|current_page_url|resize_display)\b"
        r"|\bworkspace\.status(?![\w.])"
    ),
    "names a deprecated /workspace route or operation id; new code and docs use "
    "/machines/{ref}/... (files, previews, browser) and the port proxy "
    "/public/machine-ports/<grant>/. A doc line that says the old name is "
    "deprecated passes",
    shim=True,
)
FILESYSTEM_PATH = Rule(
    "workspace-path",
    re.compile(_ROOTED),
    "uses /workspace as a filesystem path; a machine's paths come from "
    "sandbox_runtime/paths.py: home /home/user, projects ~/lemma "
    "(/home/user/lemma), browser profile ~/.lemma/browser/profile",
)
OLD_SDK = Rule(
    "old-sdk",
    re.compile(r"\bWorkspace(?:Apps)?Service\b"),
    "names a removed generated workspace client; use the machines SDK "
    "(TypeScript lemma.machines, Python lemma.machines, functions ctx.machines)",
)
OLD_SDK_ALIAS = Rule(
    "old-sdk-alias",
    re.compile(r"\blemma\.workspace\."),
    "teaches lemma.workspace.*, the deprecated alias; document lemma.machines.* "
    "instead (a line that says the alias is deprecated passes)",
    scope=DOCS_AND_SKILLS,
    shim=True,
)
AGENT_SANDBOX = Rule(
    "agent-sandbox",
    re.compile(
        r"\b(?:your|the|this) sandbox\b"
        r"|\bworkspace sandbox(?:es)?\b"
        r"|\byour workspace\b"
        r"|\bworkspace container\b",
        re.IGNORECASE,
    ),
    "says sandbox/workspace in agent-facing text where the agent's machine is "
    "meant; say 'your machine' (or name the function sandbox, if that is what "
    "it is)",
    scope=AGENT_FACING,
)

RULES: Tuple[Rule, ...] = (
    OLD_ROUTE,
    FILESYSTEM_PATH,
    OLD_SDK,
    OLD_SDK_ALIAS,
    AGENT_SANDBOX,
)


@dataclass(frozen=True)
class Allowance:
    glob: str
    rule: Optional[str]

    def covers(self, path: str, rule: str) -> bool:
        return (self.rule is None or self.rule == rule) and fnmatch.fnmatch(
            path, self.glob
        )


def parse_allowlist(text: str) -> List[Allowance]:
    """`path-or-glob[:rule]  # reason`, one per line. A reason is required."""
    allowances = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        entry, _, reason = line.partition("#")
        entry = entry.strip()
        if not reason.strip():
            raise ValueError(
                f"{ALLOW_FILE.name}:{number}: {entry!r} has no reason; write "
                "why it is legitimate after a '#'"
            )
        glob, _, rule = entry.partition(":")
        if rule and rule not in {r.name for r in RULES}:
            raise ValueError(f"{ALLOW_FILE.name}:{number}: unknown rule {rule!r}")
        allowances.append(Allowance(glob, rule or None))
    return allowances


def find_offences(path: str, text: str) -> List[Tuple[int, Rule, str]]:
    """Every stale term in one file, as (line, rule, matched text).

    A route match also matches the rooted-path rule; it is reported once, as
    the route, because that is the fix the reader needs.
    """
    rules = [rule for rule in RULES if rule.applies_to(path)]
    # A shim's own route is also a rooted /workspace; in a test of the shim
    # it must not come back as a path.
    shim_names = OLD_ROUTE if is_test(path) else None
    found = []
    for number, line in enumerate(text.splitlines(), start=1):
        claimed: List[Tuple[int, int]] = []
        if shim_names is not None:
            claimed.extend(match.span() for match in shim_names.pattern.finditer(line))
        documents_deprecation = "deprecated" in line.lower()
        for rule in rules:
            for match in rule.pattern.finditer(line):
                span = match.span()
                if any(start <= span[0] < end for start, end in claimed):
                    continue
                claimed.append(span)
                if rule.shim and documents_deprecation:
                    continue
                found.append((number, rule, match.group(0)))
    return found


def _skipped(path: str) -> bool:
    if path in (SELF, SELF_TEST, ALLOW_PATH) or path.endswith(SKIP_SUFFIXES):
        return True
    if any(part in f"/{path}" for part in SKIP_PARTS):
        return True
    return any(fnmatch.fnmatch(path, glob) for glob in SKIP_PATHS)


def candidate_files(root: Path) -> List[str]:
    """Tracked files plus untracked ones git does not ignore.

    Untracked too, so a new file is checked before anyone remembers to add it.
    """
    listed = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return sorted({name for name in listed.split("\0") if name and not _skipped(name)})


def _read(path: Path) -> Optional[str]:
    try:
        if not path.is_file() or path.stat().st_size > MAX_BYTES:
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, ValueError):
        return None


def check(
    root: Path, files: Iterable[str], allowances: Sequence[Allowance]
) -> List[str]:
    report = []
    for name in files:
        text = _read(root / name)
        if text is None:
            continue
        for number, rule, matched in find_offences(name, text):
            if any(allowance.covers(name, rule.name) for allowance in allowances):
                continue
            report.append(f"{name}:{number}: [{rule.name}] {matched!r} {rule.message}")
    return report


def main(argv: Sequence[str]) -> int:
    allowances = (
        parse_allowlist(ALLOW_FILE.read_text(encoding="utf-8"))
        if ALLOW_FILE.exists()
        else []
    )
    files = list(argv) if argv else candidate_files(ROOT)
    report = check(ROOT, files, allowances)
    if not report:
        print("✓ stale machine terms: none")
        return 0
    print("Words for the old workspace sandbox, where a machine is meant:\n")
    for line in report:
        print(f"  {line}")
    print(
        f"\n{len(report)} hit(s). Fix the text; a genuinely historical use goes in "
        f"scripts/{ALLOW_FILE.name} with its reason."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
