#!/usr/bin/env python
"""Keep internal detail out of a public repository's prose.

CONTRIBUTING's "Code and comments" already forbids internal hostnames,
incident timestamps and "what I tried" narratives. This is the part that
notices when it happens anyway -- because that rule was written down, read,
agreed with, and then broken by a change that put a dated incident narrative
and an internal cluster hostname into a docstring. Every fact in that
paragraph that made it *specific* was the part that should not ship, and
none of it was load-bearing: the docstring said everything a reader needed
once the date, the host and the play-by-play came out.

A reviewer will not reliably catch this. Prose is the part of a diff people
skim, and it is the part no other gate reads.

Two rules, scoped so that each is nearly false-positive-free:

- **Internal hostnames, anywhere in the file.** A deployment name is a leak
  in a fixture exactly as much as in a sentence -- a sweep of this repo
  found one internal host in 178 places, most of them test data.
- **Dates, in comments and docstrings only.** A date in a comment is either
  an incident timestamp or a note that is already stale. In a *string* it is
  usually sample content, so string literals are not read; and a genuinely
  public date (an incorporation date, say) goes in the allowlist beside this
  file, with its reason.

Deliberately not a general secret scanner -- gitleaks already runs, and it
scans commits rather than the tree. This looks for the two things people
write by accident while explaining a fix.
"""

from __future__ import annotations

import ast
import io
import re
import subprocess
import sys
import tokenize
from pathlib import Path

#: Hostnames that name infrastructure rather than the product. `lemma.work`
#: is the public product domain and is fine; `example.com` and
#: `example.test` are reserved for documentation by RFC 2606.
#:
#: Add a domain here when a deployment, cluster or tenant gets a name. The
#: bare entry covers `api.<host>` and `apps.<host>`, since the match is a
#: substring.
INTERNAL_HOSTS = ("asur.work", "gappynew")


def _host_pattern(host: str) -> re.Pattern[str]:
    r"""Match a host however a file happens to spell it.

    Case-insensitively, and tolerating a backslash before each dot. Both
    came from a sweep that used a plain lowercase substring and left three
    survivors: an uppercased hostname in a test asserting case-folding, and
    two `asur\.work` inside testing-library regexes. The sweep read as
    complete and CI found the rest.
    """
    return re.compile(
        r"\\?\.".join(re.escape(part) for part in host.split(".")), re.IGNORECASE
    )


HOST_PATTERNS = tuple((host, _host_pattern(host)) for host in INTERNAL_HOSTS)

_MONTHS = (
    "January|February|March|April|May|June|July|August|September|October"
    "|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept|Sep|Oct|Nov|Dec"
)
#: "On 19 September", "in Sept 2026", "since March 3". Only the shapes that
#: occur in sentences, so an ISO date, a migration id and a version string
#: are all untouched.
DATED_PROSE = re.compile(
    rf"\b(?:[Oo]n|[Ii]n|[Ss]ince|[Uu]ntil|[Bb]y|[Aa]fter|[Bb]efore)\s+"
    rf"(?:\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{_MONTHS})|(?:{_MONTHS})\s+\d{{1,2}})\b"
)

#: Every tracked file is read, and the exclusions below are named rather
#: than an allowlist of extensions being named instead. That was the first
#: shape of this and it was wrong: a hostname in a `Makefile`, a workflow,
#: a `Caddyfile` or an `.env.example` is exactly as public as one in a
#: docstring, and those are the files a deployment name is *most* likely to
#: be written into. Probed -- an internal host appended to the repository
#: `Makefile` passed the suffix-gated version without comment.
#:
#: Binary files need no rule of their own: they fail to decode, which is a
#: better "is this text" test than an extension list or a magic-byte sniff.
#: Generated clients and specs carry whatever the source said. Flagging them
#: reports one fault twice and points the fixer at a file they must not edit.
SKIP_PARTS = (
    "node_modules",
    ".generated",
    "openapi_client",
    "/build/",
    "/dist/",
    ".venv",
)
#: Locks and committed bundles are vendored bulk with no prose in them.
SKIP_NAMES = (
    "openapi_spec.json",
    "openapi.json",
    ".lock",
    "lock.json",
    "lock.yaml",
    "lemma-client.js",
    "lemma-ui.js",
)
#: Big enough for any file a person writes by hand, small enough that a
#: checked-in dataset is not read line by line on every `make quality`.
MAX_BYTES = 2 * 1024 * 1024
SELF = "check_public_prose.py"
ALLOW_FILE = Path(__file__).with_name("public-prose-allow.txt")

#: Line-comment openers for the non-Python files. Block comments are caught
#: by the continuation marker `*`, which is how they are conventionally laid
#: out; a date buried mid-block without one is the miss this accepts.
COMMENT_PREFIXES = ("//", "#", "*", "/*", "--", "<!--")


def _load_allowlist() -> set[str]:
    """Paths whose dates are known-public, one `path  # reason` per line."""
    if not ALLOW_FILE.exists():
        return set()
    allowed = set()
    for line in ALLOW_FILE.read_text(encoding="utf-8").splitlines():
        entry = line.split("#", 1)[0].strip()
        if entry:
            allowed.add(entry)
    return allowed


def tracked_files(root: Path) -> list[Path]:
    listed = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    found = []
    for name in listed.split("\0"):
        if not name or name.endswith(SKIP_NAMES):
            continue
        if any(part in f"/{name}" for part in SKIP_PARTS) or name.endswith(SELF):
            continue
        found.append(root / name)
    return found


def _python_prose_lines(text: str) -> set[int]:
    """Line numbers of comments and docstrings, and nothing else.

    `tokenize` for comments and `ast` for docstrings, rather than a regex:
    the whole point is to *not* read string literals, and telling a
    docstring from a string needs the tree.
    """
    lines: set[int] = set()
    try:
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type == tokenize.COMMENT:
                lines.add(token.start[0])
    except tokenize.TokenError, IndentationError, SyntaxError:
        return lines
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return lines
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            if isinstance(first.value.value, str):
                lines.update(
                    range(first.lineno, (first.end_lineno or first.lineno) + 1)
                )
    return lines


def _markdown_prose_lines(text: str) -> set[int]:
    """Markdown outside fenced code blocks.

    A fence holds sample commands and sample output -- an example order
    arriving "by March 15" is content, not a note about this repository.
    """
    lines: set[int] = set()
    fenced = False
    for number, line in enumerate(text.splitlines(), start=1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if not fenced:
            lines.add(number)
    return lines


def _prose_lines(path: Path, text: str) -> set[int] | None:
    """Which lines are prose. `None` means "the whole file is prose"."""
    if path.suffix == ".md":
        return _markdown_prose_lines(text)
    if path.suffix == ".py":
        return _python_prose_lines(text)
    return {
        number
        for number, line in enumerate(text.splitlines(), start=1)
        if line.strip().startswith(COMMENT_PREFIXES)
    }


def offences(path: Path, root: Path, allowed: set[str]) -> list[str]:
    try:
        if path.stat().st_size > MAX_BYTES:
            return []
        text = path.read_text(encoding="utf-8")
    except OSError, UnicodeDecodeError, ValueError:
        return []
    where = str(path.relative_to(root))
    prose = _prose_lines(path, text)
    found = []
    for number, line in enumerate(text.splitlines(), start=1):
        for host, pattern in HOST_PATTERNS:
            if pattern.search(line):
                found.append(
                    f"{where}:{number}: names internal infrastructure "
                    f"({host!r}) -- lemma.work for the product, example.com "
                    f"or example.test for a stand-in"
                )
        if where in allowed or (prose is not None and number not in prose):
            continue
        match = DATED_PROSE.search(line)
        if match:
            found.append(
                f"{where}:{number}: dates a change ({match.group(0)!r}) -- say "
                f"what the failure was, not when it happened. If the date is "
                f"genuinely public, add the path to {ALLOW_FILE.name}"
            )
    return found


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    allowed = _load_allowlist()
    found = [
        offence
        for path in tracked_files(root)
        for offence in offences(path, root, allowed)
    ]
    if not found:
        print("✓ public prose: no internal hostnames or dated narratives")
        return 0
    print("Internal detail in a public repository's prose:\n")
    for offence in found:
        print(f"  {offence}")
    print(
        "\nCONTRIBUTING, 'Code and comments': keep the investigation out of "
        "the source. The conclusion and the reason it holds survive into the "
        "comment; the date, the host and the play-by-play go in the pull "
        "request, redacted."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
