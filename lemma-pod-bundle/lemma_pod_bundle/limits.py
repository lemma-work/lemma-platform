"""Conservative default caps for what a pod bundle may carry.

A bundle ships *resources* that recreate a pod in an empty-table state, plus —
opt-in — a little seed/setup data (default table rows for UI, skill/script
files). It is deliberately NOT a bulk data or file dump: shipping large data is
discouraged and hammers the DB/object storage on both export and import.

These are the SINGLE SOURCE OF TRUTH for the numbers. The backend settings
default to them (and stay env-overridable for ops) and the CLI uses them
directly, so an API-built bundle and a CLI-built one are bounded identically.
"""

from __future__ import annotations

_MB = 1024 * 1024

# Row data (seed rows written to a table's data.csv).
MAX_RECORDS_PER_TABLE = 5_000
MAX_RECORDS_TOTAL = 10_000

# Data byte pool — a table's data.csv AND pod files draw from ONE shared budget.
MAX_ITEM_BYTES = 10 * _MB
MAX_DATA_TOTAL_BYTES = 20 * _MB

# App builds (React source/dist archives) get their OWN pool so a big app can't
# starve seed data, and vice versa.
MAX_APP_BYTES = 10 * _MB
MAX_APPS_TOTAL_BYTES = 20 * _MB

# Import-side ceiling on how many apply steps one bundle may declare (one per
# resource, file, deferred grants entry and seeded table). The export caps above
# bound what *we* write; nothing bounded what an uploaded or GitHub-fetched
# bundle asks the importer to do, so under the uncompressed-byte guard a bundle
# could still declare tens of thousands of steps. Generous next to any real pod
# — the exporter itself lists at most 1,000 of each resource type.
MAX_IMPORT_PLAN_STEPS = 5_000
