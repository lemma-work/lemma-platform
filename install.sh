#!/usr/bin/env bash
# Lemma local installer bootstrap.
#
#   curl -fsSL https://raw.githubusercontent.com/lemma-work/lemma-platform/v0.5.4/install.sh | bash
#
# Installs uv (if missing), installs lemma-stack as a uv tool, and hands off
# to `lemma-stack install`, which detects/installs a container runtime
# (podman recommended), pulls the released images, and starts the stack at
# ~/.lemma/local. Pass arguments through:
#
#   ./install.sh --runtime podman -y
#
# SECURITY (BP-003): the `uv tool install` step below is pinned to a tagged
# release, not `main`. A mutable branch would mean every fresh host running
# this one-liner executes the latest commit on `main` with the installing
# user's privileges — a compromised upstream (push to `main`, hijacked
# maintainer, tampered CI) becomes arbitrary code execution. Override the
# pin only with explicit intent — see LEMMA_STACK_REF below.
set -Eeuo pipefail

say() { printf '%s\n' "$*"; }
fail() {
  say "error: $*" >&2
  exit 1
}

export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

if ! command -v uv >/dev/null 2>&1; then
  say "Installing uv (https://astral.sh/uv)…"
  command -v curl >/dev/null 2>&1 || fail "curl is required; install curl and re-run"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
  command -v uv >/dev/null 2>&1 || fail "uv installed but not on PATH; open a new shell and re-run"
fi

# LEMMA_STACK_PIN is the upstream release tag we install from. Hard-pinning
# (rather than a moving ref like `main` or `releases/latest`) means every
# fresh host gets an immutable, named artifact — easy to audit, easy to
# reproduce, easy to diff. To ship a new release: bump this tag, cut the
# GitHub release, that's it.
LEMMA_STACK_PIN="${LEMMA_STACK_PIN:-v0.5.4}"

# Overrides — opt-in only, never the default for first-time users:
#   LEMMA_STACK_SOURCE=/abs/path/to/lemma-stack     ./install.sh -y
#     → install from a local checkout (uses uv's source/ editable install).
#   LEMMA_STACK_REF=v0.5.3                          ./install.sh -y
#     → pin to a specific tag (release downgrade or reproducibility).
#   LEMMA_STACK_REF=main                            ./install.sh -y
#     → EXPLICIT canary opt-in against `main`. You're opting into the same
#       mutable-ref risk this script otherwise defends against; don't do
#       this on a shared / production host.
if [ -n "${LEMMA_STACK_SOURCE:-}" ]; then
  LEMMA_STACK_SPEC="${LEMMA_STACK_SOURCE}"
else
  LEMMA_STACK_REF="${LEMMA_STACK_REF:-${LEMMA_STACK_PIN}}"
  LEMMA_STACK_SPEC="git+https://github.com/lemma-work/lemma-platform.git@${LEMMA_STACK_REF}#subdirectory=lemma-stack"
fi

say "Installing lemma-stack…"
say "  source: ${LEMMA_STACK_SPEC}"
uv tool install --force "$LEMMA_STACK_SPEC" >/dev/null
command -v lemma-stack >/dev/null 2>&1 || export PATH="$(uv tool dir --bin 2>/dev/null || echo "$HOME/.local/bin"):$PATH"
command -v lemma-stack >/dev/null 2>&1 || fail "lemma-stack installed but not on PATH; run: uv tool update-shell"

exec lemma-stack install "$@"
