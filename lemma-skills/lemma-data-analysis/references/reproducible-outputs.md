# Reproducible Outputs

Make the path from raw source to headline result inspectable and rerunnable.

## Organize the local work

Use one task folder with clear separation:

```text
analysis-<slug>/
├── inputs/       # immutable exports/downloads
├── src/          # SQL and Python
└── outputs/      # report, result tables, charts, workbook
```

Use stable filenames. Do not overwrite raw inputs. Put assumptions and metric
definitions in the report or source code, not only in chat. Pin randomness with
an explicit seed when simulation, sampling, or stochastic models are involved.

## Use the workspace Python stack

Confirm the intended runtime before analysis:

```bash
python3 -c 'import pandas, numpy, matplotlib, openpyxl; print(pandas.__version__, numpy.__version__, matplotlib.__version__, openpyxl.__version__)'
```

Use:

- `pandas` to read CSV/JSON/XLSX, normalize types, join, aggregate, and emit
  machine-readable result tables;
- `numpy` for vectorized numerical work, reproducible simulation, and numerical
  checks;
- `matplotlib` for deterministic PNG/SVG figures with explicit labels and sizes;
- `openpyxl` to create or edit XLSX files when formulas, styles, merged cells,
  widths, formats, or workbook structure must be preserved.

Do not round intermediate values. Parse datetimes explicitly, localize or convert
timezones deliberately, and preserve raw identifiers as strings. If editing an
existing workbook, use `openpyxl` rather than a pandas round-trip that drops
formulas or formatting. Keep transformation logic in a script; do not rely on
manual cell changes. `openpyxl` does not calculate formulas: compute verified
headline values in Python or recalculate with a spreadsheet engine before
claiming formula results are validated. Use a non-interactive Matplotlib backend
for headless runs.

For a reproducible SQL result, call the CLI without a shell and retain the SQL in
source code:

```python
import json
import subprocess

import pandas as pd

sql = "SELECT status, COUNT(*) AS total FROM orders GROUP BY status"
completed = subprocess.run(
    ["lemma", "--output", "json", "query", "run", sql],
    check=True,
    capture_output=True,
    text=True,
)
payload = json.loads(completed.stdout)
result = pd.DataFrame(payload["items"])
```

Never embed tokens or pod IDs when the injected Lemma context already supplies
them. Fail loudly on nonzero commands, unexpected schemas, empty required
populations, duplicate keys, or reconciliation mismatches.

## Build the analytical package

Include only artifacts needed to understand or reproduce the answer:

- `report.md`: answer first, supporting evidence, recommendation, definitions,
  scope, uncertainty, and limitations;
- `analysis.py` and/or `queries.sql`: exact transformations and source queries;
- `results.csv`: the values behind headline metrics and charts;
- chart PNG/SVG files and, when requested, an XLSX workbook;
- a small provenance section in `report.md` with snapshot time, sources, RLS
  scope, row counts, and runtime/package versions.

Render charts at a legible size, save with a tight layout, and inspect the actual
PNG with the image-viewing capability before delivery. Open generated workbooks
and check sheet names, values, formulas, number formats, frozen panes, filters,
column widths, and error cells. Re-run the script from the immutable inputs and
compare headline results before publishing.

## Publish to Lemma

Use a private path by default:

```bash
lemma files mkdir /me/analysis
lemma files mkdir /me/analysis/<slug>
lemma files upload ./outputs/report.md /me/analysis/<slug>/report.md
lemma files upload ./src/analysis.py /me/analysis/<slug>/analysis.py --no-search
lemma files upload ./outputs/results.csv /me/analysis/<slug>/results.csv --no-search
lemma files upload ./outputs/chart.png /me/analysis/<slug>/chart.png --no-search
lemma files upload ./outputs/analysis.xlsx /me/analysis/<slug>/analysis.xlsx --no-search
lemma files stat /me/analysis/<slug>/report.md
lemma files url /me/analysis/<slug>/report.md
```

`/me` is private to the acting identity; another pod member cannot use it to
rerun the package. Upload to a specifically authorized shared pod folder only
when the user requests collaborator access. Identical code run by another user
may produce different RLS-scoped data; describe that boundary rather than
claiming identical results. Never move a raw RLS extract into a shared folder
unless the recipient is explicitly authorized for those rows; prefer aggregate
or de-identified reproducibility artifacts when suitable.

Markdown reports are indexed; CSV, JSON, XLSX, and images are stored but not
indexed, so `--no-search` makes that intent explicit. Upload source data only
when necessary and authorized. Redact secrets and minimize personal data. Use
`lemma files share` only when the user explicitly needs a bounded public link.
