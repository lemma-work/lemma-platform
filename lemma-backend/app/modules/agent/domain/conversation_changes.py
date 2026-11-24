"""What a repository conversation's checkout has changed, and how that is read.

A conversation opened in a GitHub repository works in a checkout of its own
(``project_repo``): on the person's machine a worktree on ``lemma/{slug}``, on a
pod machine a detached checkout in the run's directory. A Changes card wants to
show what the agent has done there before it pushes: every file changed since
the conversation's start point, with its line counts and, if asked, the patch.

Everything here is pure: the one shell command that reads it, and the parser
for what that command prints. Running it on the right machine, and deciding
whether it may run at all, is ``application/conversations/changes``.

**Against what.** The merge base of ``HEAD`` with the conversation's start
point -- ``origin/<ref>`` (or the ref itself, for a tag or a commit) when it
was opened on one, else ``origin/HEAD`` -- which is where its branch left the
line it came from. The diff runs from there to the working tree, so commits
not yet pushed, staged and unstaged edits all count, and untracked files are
listed beside them. Nothing is fetched first: ``origin/*`` is whatever the
checkout's last fetch left, and fetching is a write.

**Read only.** The command never writes to the repository: no fetch, no index
refresh (``GIT_OPTIONAL_LOCKS=0`` and ``--no-optional-locks``), and nothing
from the repository's own configuration that would run a program -- external
diff drivers, text conversion filters, an fsmonitor hook -- since a checkout
an agent works in can configure any of them. Its scratch output goes to a
temporary directory outside the checkout, removed on exit.

**Framing.** Each part of the output is one line, a name and the part
base64-encoded, so whatever bytes a path or a patch holds -- and however the
transport decodes stdout -- the parts come back intact and their sizes are
the sizes the caps were applied to.
"""

from __future__ import annotations

import base64
import binascii
import shlex
from dataclasses import dataclass, field
from typing import Literal

from app.modules.agent.domain.project_repo import ProjectRepo

ChangesState = Literal[
    "ready", "no_repository", "not_checked_out", "machine_unavailable", "elsewhere"
]
FileStatus = Literal["added", "modified", "deleted", "renamed", "untracked"]

#: The patch's size when nobody asks for less, and the most anybody may ask
#: for. Both are under the sandbox runtime's own output ceiling with base64
#: and the file lists on top.
DEFAULT_PATCH_BYTES = 256 * 1024
MAX_PATCH_BYTES = 1024 * 1024
#: The file lists (tracked changes, their statuses) are cut at this many bytes
#: each: a checkout with a vendored tree committed on its branch still answers.
LIST_BYTES = 256 * 1024
#: Untracked files counted and listed, at most. ``--exclude-standard`` keeps a
#: dependency tree out when the repository ignores it; this, when it does not.
MAX_UNTRACKED = 200

#: The command's own exit status for a directory that is not a checkout.
NOT_CHECKED_OUT_EXIT = 64

_GIT = (
    "git --no-optional-locks -c core.quotepath=off -c core.fsmonitor=false "
    "-c color.ui=never"
)
_DIFF = "diff --no-ext-diff --no-textconv --no-color"
_FORMAT_VERSION = "lemma-changes 1"


@dataclass(frozen=True, slots=True)
class ChangedFile:
    path: str
    status: FileStatus
    additions: int = 0
    deletions: int = 0
    binary: bool = False
    old_path: str | None = None


@dataclass(frozen=True, slots=True)
class ConversationChanges:
    state: ChangesState
    branch: str | None = None
    base_ref: str | None = None
    base_sha: str | None = None
    head_sha: str | None = None
    ahead: int = 0
    files: tuple[ChangedFile, ...] = ()
    uncommitted: bool = False
    patch: str | None = None
    truncated: bool = False

    @property
    def additions(self) -> int:
        return sum(changed.additions for changed in self.files)

    @property
    def deletions(self) -> int:
        return sum(changed.deletions for changed in self.files)


def checkout_dir(repo: ProjectRepo, cwd: str, *, run_workdir: str | None) -> str:
    """Where the conversation's checkout of ``repo`` is, on the machine it runs on.

    The same answer ``github_project`` and ``run_machine`` give when they make
    it: the run's own copy on a shared machine, else the conversation's
    worktree, else -- a conversation from before worktrees, or a directory set
    by hand -- the clone itself.
    """
    if run_workdir is not None:
        return f"{run_workdir}/repos/{repo.owner}/{repo.repo}"
    slug = repo.worktree_slug(cwd)
    return repo.worktree_dir(slug) if slug is not None else repo.clone_dir


def changes_command(*, directory: str, ref: str | None, patch_bytes: int | None) -> str:
    """The one command that reads a checkout's changes. See the module.

    ``patch_bytes`` is None for no patch. Exits ``NOT_CHECKED_OUT_EXIT`` when
    ``directory`` holds no checkout; otherwise 0 with every part printed.
    """
    target = ref or "HEAD"
    remote = shlex.quote(f"origin/{target}")
    fallback = shlex.quote(ref) if ref else "''"
    steps = [
        f"[ -e {shlex.quote(directory + '/.git')} ] || exit {NOT_CHECKED_OUT_EXIT}",
        f"cd {shlex.quote(directory)} || exit {NOT_CHECKED_OUT_EXIT}",
        "export GIT_OPTIONAL_LOCKS=0 GIT_TERMINAL_PROMPT=0",
        f'g() {{ {_GIT} "$@"; }}',
        "t=$(mktemp -d) || exit 70",
        "trap 'rm -rf \"$t\"' EXIT",
        "head=$(g rev-parse --verify --quiet HEAD) || head=",
        "branch=$(g symbolic-ref --quiet --short HEAD) || branch=",
        f"start={remote}",
        (
            'g rev-parse --verify --quiet "$start^{commit}" >/dev/null '
            f"|| start={fallback}"
        ),
        (
            '[ -z "$start" ] || g rev-parse --verify --quiet "$start^{commit}" '
            ">/dev/null || start="
        ),
        # Named as the branch origin/HEAD points at, which a card can show.
        (
            '[ "$start" != origin/HEAD ] || start=$(g symbolic-ref --quiet --short '
            "refs/remotes/origin/HEAD) || start=origin/HEAD"
        ),
        (
            'base=; [ -z "$start" ] || [ -z "$head" ] '
            '|| base=$(g merge-base "$start" HEAD) || base='
        ),
        'ahead=0; [ -z "$base" ] || ahead=$(g rev-list --count "$base..HEAD")',
        "empty=$(g hash-object -t tree /dev/null)",
        "from=${base:-${head:-$empty}}",
        'dirty=0; [ -z "$head" ] || g diff --quiet --no-ext-diff HEAD -- || dirty=1',
        (
            f'g {_DIFF} --numstat -z -M "$from" -- | head -c {LIST_BYTES + 1} '
            '> "$t/numstat"'
        ),
        (
            f'g {_DIFF} --name-status -z -M "$from" -- | head -c {LIST_BYTES + 1} '
            '> "$t/status"'
        ),
        _untracked_step(),
        _patch_step(patch_bytes),
        (
            "printf 'branch=%s\\nstart=%s\\nbase=%s\\nhead=%s\\nahead=%s\\n"
            "dirty=%s\\nmore=%s\\n' "
            '"$branch" "$start" "$base" "$head" "$ahead" "$dirty" "$more" > "$t/meta"'
        ),
        f"echo {shlex.quote(_FORMAT_VERSION)}",
        (
            "for part in meta numstat status untracked patch; do "
            '[ -e "$t/$part" ] || continue; '
            "printf '%s ' \"$part\"; base64 < \"$t/$part\" | tr -d '\\n'; echo; done"
        ),
    ]
    return "; ".join(steps)


def _untracked_step() -> str:
    """Each untracked file and its line counts, ``path\\0adds\\tdels\\0``, for
    the first ``MAX_UNTRACKED``; ``more=1`` if there were others.

    A directory entry is a nested repository, listed with nothing counted.
    """
    count = (
        f'c=$(g {_DIFF} --no-index --numstat -- /dev/null "$f" | head -n 1 | cut -f1,2)'
    )
    return (
        'more=0; n=0; : > "$t/untracked"; '
        "while IFS= read -r -d '' f; do "
        f'if [ "$n" -ge {MAX_UNTRACKED} ]; then more=1; break; fi; n=$((n+1)); '
        f'case "$f" in */) c="0\t0" ;; *) {count} ;; esac; '
        'printf \'%s\\0%s\\0\' "$f" "${c:-0\t0}" >> "$t/untracked"; '
        "done < <(g ls-files --others --exclude-standard -z)"
    )


def _patch_step(patch_bytes: int | None) -> str:
    if patch_bytes is None:
        return ":"
    untracked = (
        "while IFS= read -r -d '' f; do "
        f'case "$f" in */) ;; *) g {_DIFF} --no-index -- /dev/null "$f" ;; esac; '
        "IFS= read -r -d '' _; "
        'done < "$t/untracked"'
    )
    return (
        f'{{ g {_DIFF} -M "$from" --; {untracked}; }} '
        f'| head -c {patch_bytes + 1} > "$t/patch"'
    )


class ChangesOutputError(ValueError):
    """The command's output is not what it prints. Never a person's doing."""


@dataclass(slots=True)
class _Parts:
    meta: dict[str, str] = field(default_factory=dict)
    numstat: bytes = b""
    status: bytes = b""
    untracked: bytes = b""
    patch: bytes | None = None


def parse_changes(stdout: str, *, patch_bytes: int | None) -> ConversationChanges:
    """What ``changes_command`` printed, as a ``ready`` answer.

    A checkout whose start point is not there to compare against -- never
    fetched, or a ref since deleted -- has no base: what it lists is its
    uncommitted work alone, and ``base_ref`` says so by being empty.
    """
    parts = _parts(stdout)
    meta = parts.meta
    numstat, numstat_cut = _records(parts.numstat)
    status, status_cut = _records(parts.status)
    tracked = _tracked_files(numstat, status)
    untracked_records, untracked_cut = _records(parts.untracked)
    untracked = _untracked_files(untracked_records)
    patch, patch_cut = _patch(parts.patch, patch_bytes)
    base = meta.get("base") or None
    return ConversationChanges(
        state="ready",
        branch=meta.get("branch") or None,
        base_ref=(meta.get("start") or None) if base else None,
        base_sha=base,
        head_sha=meta.get("head") or None,
        ahead=_int(meta.get("ahead")) if base else 0,
        files=tuple(tracked + untracked),
        uncommitted=meta.get("dirty") == "1" or bool(untracked),
        patch=patch,
        truncated=any((numstat_cut, status_cut, untracked_cut, patch_cut))
        or meta.get("more") == "1",
    )


def _parts(stdout: str) -> _Parts:
    # A login shell's profile may print before the command does.
    lines = stdout.splitlines()
    try:
        first = [line.strip() for line in lines].index(_FORMAT_VERSION)
    except ValueError:
        raise ChangesOutputError("the changes command printed no header") from None
    parts = _Parts()
    for line in lines[first + 1 :]:
        name, _, encoded = line.partition(" ")
        try:
            value = base64.b64decode(encoded.strip(), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ChangesOutputError(f"part {name!r} is not base64") from exc
        if name == "meta":
            parts.meta = _meta(value)
        elif name == "numstat":
            parts.numstat = value
        elif name == "status":
            parts.status = value
        elif name == "untracked":
            parts.untracked = value
        elif name == "patch":
            parts.patch = value
    if not parts.meta:
        raise ChangesOutputError("the changes command printed no meta part")
    return parts


def _meta(value: bytes) -> dict[str, str]:
    meta: dict[str, str] = {}
    for line in value.decode("utf-8", "replace").splitlines():
        key, _, rest = line.partition("=")
        meta[key] = rest.strip()
    return meta


def _records(value: bytes) -> tuple[list[str], bool]:
    """NUL-terminated records, and whether the part was cut at ``LIST_BYTES``.

    A cut part ends mid-record; that record is dropped rather than read as a
    shorter path.
    """
    cut = len(value) > LIST_BYTES
    if cut:
        value = value[:LIST_BYTES]
    records = value.split(b"\0")
    # Complete records end in NUL, so the last piece is the partial one (or
    # empty when nothing was cut).
    # "replace", not "surrogateescape": a lone surrogate cannot be written as
    # JSON, and a path that is not UTF-8 is rare enough to show mangled.
    return [record.decode("utf-8", "replace") for record in records[:-1]], cut


def _count(value: str) -> tuple[int, int, bool]:
    """``adds`` and ``dels`` from numstat, and whether git called it binary."""
    adds, _, dels = value.partition("\t")
    if adds == "-" or dels == "-":
        return 0, 0, True
    return _int(adds), _int(dels), False


def _int(value: str | None) -> int:
    try:
        return max(0, int(value or 0))
    except ValueError:
        return 0


def parse_numstat(records: list[str]) -> list[tuple[str, str | None, int, int, bool]]:
    """``git diff --numstat -z -M``: ``(path, old_path, adds, dels, binary)``.

    A plain entry is one record, ``adds\\tdels\\tpath``. A rename leaves the
    path empty and puts the old and new paths in the two records after it.
    """
    entries: list[tuple[str, str | None, int, int, bool]] = []
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        fields = record.split("\t", 2)
        if len(fields) != 3:
            continue
        adds, dels, path = fields
        count = _count(f"{adds}\t{dels}")
        if path:
            entries.append((path, None, *count))
            continue
        if index + 1 >= len(records):
            break
        old, new = records[index], records[index + 1]
        index += 2
        entries.append((new, old, *count))
    return entries


def parse_name_status(records: list[str]) -> dict[str, tuple[FileStatus, str | None]]:
    """``git diff --name-status -z -M``: each new path's status and old path.

    A status is one record; a rename or a copy takes two paths after it, every
    other status one.
    """
    statuses: dict[str, tuple[FileStatus, str | None]] = {}
    index = 0
    while index < len(records):
        code = records[index]
        index += 1
        letter = code[:1]
        if letter in ("R", "C"):
            if index + 1 >= len(records):
                break
            old, new = records[index], records[index + 1]
            index += 2
            statuses[new] = ("renamed" if letter == "R" else "added", old)
            continue
        if index >= len(records):
            break
        path = records[index]
        index += 1
        statuses[path] = (_STATUS.get(letter, "modified"), None)
    return statuses


_STATUS: dict[str, FileStatus] = {"A": "added", "D": "deleted", "M": "modified"}


def _tracked_files(numstat: list[str], status: list[str]) -> list[ChangedFile]:
    statuses = parse_name_status(status)
    files: list[ChangedFile] = []
    for path, old_path, adds, dels, binary in parse_numstat(numstat):
        kind, status_old = statuses.get(
            path, ("renamed" if old_path else "modified", old_path)
        )
        files.append(
            ChangedFile(
                path=path,
                status=kind,
                additions=adds,
                deletions=dels,
                binary=binary,
                old_path=old_path or status_old,
            )
        )
    return files


def _untracked_files(records: list[str]) -> list[ChangedFile]:
    """Pairs of ``path`` and ``adds\\tdels``, as ``_untracked_step`` writes them."""
    files: list[ChangedFile] = []
    for path, counts in zip(records[::2], records[1::2]):
        adds, dels, binary = _count(counts)
        files.append(
            ChangedFile(
                path=path.rstrip("/"),
                status="untracked",
                additions=adds,
                deletions=dels,
                binary=binary,
            )
        )
    return files


def _patch(value: bytes | None, patch_bytes: int | None) -> tuple[str | None, bool]:
    if patch_bytes is None or value is None:
        return None, False
    cut = len(value) > patch_bytes
    if cut:
        value = value[:patch_bytes]
    return value.decode("utf-8", "replace"), cut
