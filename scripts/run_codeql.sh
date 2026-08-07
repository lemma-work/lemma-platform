#!/usr/bin/env bash
# Run the CodeQL suites GitHub runs, locally, before the PR does it for you.
#
# CI surfaces two separate CodeQL products: the security alerts from
# `github/codeql-action`, and the review comments from GitHub Code Quality.
# Both draw from the same query packs, and `security-and-quality` is the suite
# that contains both sets -- so one local run covers what both would say.
#
# By default only findings in files you have changed against the base branch are
# reported, because the repository has pre-existing results that are not this
# change's to fix. Pass --all to see everything.
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
languages=""
base_ref="${CODEQL_DIFF_BASE:-origin/main}"
scope="diff"
db_root="${CODEQL_DB_ROOT:-$repo_root/.codeql}"

usage() {
  cat >&2 <<'USAGE'
usage: run_codeql.sh [--language python|javascript-typescript] [--all] [--base <ref>]

  --language  Analyse one language (default: both).
  --all       Report every finding, not just those in changed files.
  --base      Diff against this ref (default: origin/main, or $CODEQL_DIFF_BASE).

Results are cached under .codeql/; delete that directory to force a rebuild.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --language) languages="$2"; shift 2 ;;
    --all) scope="all"; shift ;;
    --base) base_ref="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage; exit 2 ;;
  esac
done

if ! command -v codeql >/dev/null 2>&1; then
  cat >&2 <<'MISSING'
codeql is not installed.

  macOS:  brew install codeql
  Linux:  https://github.com/github/codeql-cli-binaries/releases

The first run then downloads the query packs (a few hundred MB) and takes
several minutes; later runs reuse both the packs and the database.
MISSING
  exit 127
fi

if [[ -z "$languages" ]]; then
  languages="python javascript-typescript"
fi

changed_files="$db_root/changed-files.txt"
mkdir -p "$db_root"
if [[ "$scope" == "diff" ]]; then
  if ! git -C "$repo_root" rev-parse --verify --quiet "$base_ref" >/dev/null; then
    echo "base ref '$base_ref' does not exist; run with --all or pass --base" >&2
    exit 2
  fi
  # Three-dot: what this branch changed, not what the base moved on to.
  git -C "$repo_root" diff --name-only --diff-filter=d "$base_ref...HEAD" \
    > "$changed_files"
  echo "Scope: $(wc -l < "$changed_files" | tr -d ' ') file(s) changed against $base_ref"
else
  : > "$changed_files"
  echo "Scope: the whole repository"
fi

status=0
for language in $languages; do
  db="$db_root/db-$language"
  sarif="$db_root/$language.sarif"

  # A cached database is a snapshot, so reusing one older than the sources
  # reports findings you have already fixed. That is worse than being slow:
  # it is confidently wrong. Rebuild whenever any tracked file is newer.
  stale=0
  if [[ -d "$db" ]]; then
    # `head` would close the pipe early and, under `pipefail`, turn a normal
    # SIGPIPE into a failed run. sort -rn straight into a max is enough.
    newest="$(git -C "$repo_root" ls-files -z \
      | xargs -0 stat -f '%m' 2>/dev/null \
      | sort -rn | awk 'NR==1{print; exit_code=0} END{}')"
    built="$(stat -f '%m' "$db" 2>/dev/null || echo 0)"
    if [[ -n "$newest" && "$newest" -gt "$built" ]]; then
      stale=1
      echo "==> Sources changed since the cached $language database was built"
    fi
  fi

  if [[ ! -d "$db" || "$stale" == "1" ]]; then
    echo "==> Building the $language database (minutes; cached until sources change)"
    # build-mode none matches .github/workflows/security.yml, which is what
    # makes a local run comparable to CI's rather than merely similar.
    codeql database create "$db" \
      --language="$language" \
      --build-mode=none \
      --source-root="$repo_root" \
      --codescanning-config="$repo_root/.github/codeql-config.yml" \
      --overwrite >/dev/null
  else
    echo "==> Reusing the cached $language database ($db)"
  fi

  suite_language="$language"
  [[ "$language" == "javascript-typescript" ]] && suite_language="javascript"

  echo "==> Analysing $language with security-and-quality"
  codeql database analyze "$db" \
    "codeql/${suite_language}-queries:codeql-suites/${suite_language}-security-and-quality.qls" \
    --format=sarif-latest \
    --output="$sarif" \
    --download >/dev/null

  python3 "$repo_root/scripts/summarize_codeql.py" \
    --sarif "$sarif" \
    --changed-files "$changed_files" \
    --scope "$scope" \
    --allow "$repo_root/.codeql-allow.txt" || status=1
done

exit "$status"
