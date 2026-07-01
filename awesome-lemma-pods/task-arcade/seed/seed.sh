#!/bin/bash
set -euo pipefail

# ─────────────────────────────────────────────────────────────────────────────
# Task Arcade — Seed Script (optional demo data)
#
# Populates a Task Arcade pod with a demo team + world so the 3D view looks
# alive on first open. Opt-in — the pod ships empty by default.
#
# ⚠️  This script DROPS and recreates the tasks table (schema can't be mutated
#     in-place). Never run it on a pod with real data you want to keep.
#
# Usage:
#   ./seed/seed.sh                    # uses $LEMMA_POD_ID
#   ./seed/seed.sh <pod-id>           # explicit pod id
#
# Prereqs:
#   - lemma CLI installed + authenticated (lemma auth login)
#   - Pod already created + bundle imported (lemma pods import .)
# ─────────────────────────────────────────────────────────────────────────────

POD_ID="${1:-${LEMMA_POD_ID:-}}"
if [ -z "$POD_ID" ]; then
  echo "Error: no pod id. Pass it as an argument or set LEMMA_POD_ID."
  echo "  ./seed/seed.sh <pod-id>"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BUNDLE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SEED_DIR="$SCRIPT_DIR"

echo "════════════════════════════════════════════════════════════════"
echo "  Task Arcade — Seed Script"
echo "  Pod: $POD_ID"
echo "════════════════════════════════════════════════════════════════"

# ── Step 0: Drop tables that need a clean slate ─────────────────────────────
echo ""
echo "▸ Step 0: Dropping tasks table (schema reset)..."
lemma tables delete tasks --pod "$POD_ID" --yes 2>/dev/null || true
echo "  ✓ tasks dropped"

# ── Step 1: Re-import the tasks table schema ────────────────────────────────
echo ""
echo "▸ Step 1: Re-importing tasks table schema..."
lemma pods import "$BUNDLE_DIR/tables/tasks" --pod "$POD_ID"
echo "  ✓ tasks table (unified schema)"

# ── Step 2: Import functions (immutable — recreate if they don't exist) ─────
echo ""
echo "▸ Step 2: Importing functions..."
for fn in assign_task clear_task place_component review_task; do
  lemma functions delete "$fn" --pod "$POD_ID" -y 2>/dev/null || true
  lemma pods import "$BUNDLE_DIR/functions/$fn" --pod "$POD_ID"
  echo "  ✓ $fn"
done

# ── Step 3: Import agent + schedules ─────────────────────────────────────────
echo ""
echo "▸ Step 3: Importing agent + schedules..."
lemma pods import "$BUNDLE_DIR/agents/quartermaster" --pod "$POD_ID"
echo "  ✓ quartermaster agent"
lemma pods import "$BUNDLE_DIR/schedules/milestone_check" --pod "$POD_ID"
echo "  ✓ milestone_check schedule"

# ── Step 4: Seed data ────────────────────────────────────────────────────────
echo ""
echo "▸ Step 4: Seeding demo data..."

lemma records import sprints "$SEED_DIR/seed-sprints.jsonl" --pod "$POD_ID"
echo "  ✓ sprints seeded"

lemma records import team_members "$SEED_DIR/seed-team-members.jsonl" --pod "$POD_ID"
echo "  ✓ team members seeded"

lemma records import catalogue_items "$SEED_DIR/seed-catalogue-items.jsonl" --pod "$POD_ID"
echo "  ✓ catalogue items seeded"

lemma records import tasks "$SEED_DIR/seed-tasks.jsonl" --pod "$POD_ID"
echo "  ✓ tasks seeded"

# ── Step 5: Verify ───────────────────────────────────────────────────────────
echo ""
echo "▸ Step 5: Verifying..."

SPRINT_COUNT=$(lemma query run "SELECT count(*) as n FROM sprints" --pod "$POD_ID" 2>/dev/null | tail -1 || echo "?")
echo "  Sprints: $SPRINT_COUNT"

MEMBER_COUNT=$(lemma query run "SELECT count(*) as n FROM team_members" --pod "$POD_ID" 2>/dev/null | tail -1 || echo "?")
echo "  Team members: $MEMBER_COUNT"

CATALOGUE_COUNT=$(lemma query run "SELECT count(*) as n FROM catalogue_items" --pod "$POD_ID" 2>/dev/null | tail -1 || echo "?")
echo "  Catalogue items: $CATALOGUE_COUNT"

TASK_COUNT=$(lemma query run "SELECT count(*) as n FROM tasks" --pod "$POD_ID" 2>/dev/null | tail -1 || echo "?")
echo "  Tasks: $TASK_COUNT"

echo ""
echo "════════════════════════════════════════════════════════════════"
echo "  ✅ Seed complete!"
echo "  Next: rebuild + deploy the app"
echo "    cd apps/task-arcade/source"
echo "    npm install && npm run build"
echo "    lemma apps deploy task-arcade ./dist --pod $POD_ID --yes"
echo "════════════════════════════════════════════════════════════════"
