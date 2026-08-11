# Benchmark corpus (local only — never committed)

100 significant LLM / deep-learning papers downloaded from arXiv, used to
benchmark and load-test document processing. Roughly 310 MB.

This directory is gitignored on purpose. The bytes are reproducible, so the
*script* is the source of truth for what belongs here, not the files:

```bash
cd lemma-backend
uv run python load_tests/fetch_arxiv_corpus.py            # rebuild all 100
uv run python load_tests/fetch_arxiv_corpus.py --limit 20 # smaller set
```

Re-running is cheap: existing files are skipped. `manifest.json` records the
arXiv id, slug, size and download status of every paper.

## Why real papers

Extraction cost is driven by page count, column layout, table density and figure
count. Synthetic PDFs have none of that variety. This set spans ~4 to ~100 pages,
single and double column, table-heavy and figure-heavy documents — which is what
makes throughput, peak-RSS and quality numbers mean anything.

## What uses it

| Tool | Question it answers |
|---|---|
| `load_tests/extractor_ab.py` | How do two extractor configs/engines compare on speed, tables, images? (disables the extractor's result cache, so repeat runs are meaningful) |
| `load_tests/datastore_ingestion.py` | How fast does a corpus index end to end, and does anything fail? |
| `load_tests/ingestion_isolation_probe.py` | Under a burst, does interactive latency degrade, and does one pod starve another? |

Point any of them at a subset with `--corpus`/`--fixtures-dir` and `--count`/`--limit`
when you want a quick run.
