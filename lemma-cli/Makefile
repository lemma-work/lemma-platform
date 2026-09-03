# bash, not zsh: CI runners ship bash but not zsh.
SHELL := /bin/bash

# ──────────────────────────────────────────────────────────────────────────────
# Lemma CLI — local developer commands
#
#   make test         run unit + e2e tests
#   make test-unit    fast unit tests (fake SDK clients; no network, no docker)
#   make test-e2e     end-to-end tests (real backend + docker: postgres/redis/
#                     supertokens). Drives the CLI over TCP against a subprocess
#                     uvicorn server. Requires docker.
#   make coverage     unit coverage report
#   make lint         ruff check
#   make typecheck    basedpyright over lemma_cli (advisory in CI, real here)
#   make structure    file size / complexity / untyped escapes vs the baseline
#   make sync-local   rebuild the sibling path dependencies
# ──────────────────────────────────────────────────────────────────────────────

.PHONY: help test test-unit test-e2e coverage lint typecheck structure sync-local

help:
	@echo "Lemma CLI:"
	@echo "  make test          run unit + e2e tests"
	@echo "  make test-unit     fast unit tests (no docker)"
	@echo "  make test-e2e      e2e tests (real backend + docker)"
	@echo "  make coverage      unit coverage report"
	@echo "  make lint          ruff check"
	@echo "  make typecheck     basedpyright over lemma_cli"
	@echo "  make structure     size/complexity/untyped escapes vs the baseline"
	@echo "  make sync-local    rebuild the sibling path dependencies"

# lemma-sdk and lemma-pod-bundle resolve from sibling checkouts and are
# installed NON-editable on purpose (see [tool.uv.sources] — editable installs
# cost seconds of `lemma` startup in the sandbox runtime). The price is that
# `uv sync` will not rebuild them while their version string is unchanged, so
# edits over there silently do not reach these tests: the suite goes red (or,
# worse, green) against a copy that no longer matches the source. CI never sees
# it because CI installs into a fresh environment every run.
#
# Rebuilding both takes about a second, which is cheaper than the confusion.
sync-local:
	@uv sync --quiet --reinstall-package lemma-sdk --reinstall-package lemma-pod-bundle

test: test-unit test-e2e
	@echo ""
	@echo "✓ lemma-cli unit + e2e tests complete."

test-unit: sync-local
	@echo "→ lemma-cli unit tests…"
	uv run pytest -m "not e2e" -q

test-e2e: sync-local
	@echo "→ lemma-cli e2e tests (real backend + docker)…"
	uv run pytest -m e2e -q

coverage: sync-local
	@echo "→ lemma-cli unit coverage…"
	uv run --with pytest-cov pytest -m "not e2e" \
		--cov=lemma_cli --cov-report=term-missing -q

lint: sync-local
	uv run ruff check .

# The repo-level gate (`make measure-clients` at the root) is advisory and
# prints a count. These two print the findings, because the count is for
# whoever is watching the trend and the findings are for whoever is fixing
# one. Nothing had ever type-checked this package before; expect the first
# run to name things that have been wrong for a long time.
typecheck: sync-local
	uv run basedpyright lemma_cli

structure:
	uv run python ../scripts/check_client_structure.py
