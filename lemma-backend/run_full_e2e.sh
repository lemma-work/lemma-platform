#!/usr/bin/env bash
# Full e2e run for final coverage. Fast suite (parallel) then runtime suite
# (serial — real worker/docker/provider). Appends to the unit .coverage so the
# final report is combined unit+e2e. Never aborts on test failure.
set -u
cd "$(dirname "$0")"

echo "===== PHASE 1: fast e2e (parallel) ====="
uv run pytest \
  -m "e2e and not slow and not worker and not workspace and not provider and not local_cli" \
  -n 2 --dist loadscope \
  --cov=app --cov-append --cov-report= \
  -p no:cacheprovider -q 2>&1 | tail -40
echo "PHASE1_EXIT=${PIPESTATUS[0]}"

echo "===== PHASE 2: runtime e2e (serial; worker/workspace/provider/local_cli) ====="
LEMMA_RUN_PROVIDER_E2E=1 uv run pytest \
  -m "e2e and (slow or worker or workspace or provider or local_cli)" \
  --cov=app --cov-append \
  --cov-report=term:skip-covered --cov-report=xml:coverage-e2e.xml \
  -p no:cacheprovider -q 2>&1 | tail -60
echo "PHASE2_EXIT=${PIPESTATUS[0]}"

echo "===== COMBINED coverage (unit + e2e) ====="
uv run coverage report 2>/dev/null | tail -1
echo "===== DONE ====="
