#!/usr/bin/env python3
"""A/B one extractor config against another on real PDFs.

Built to answer a specific question: what does `layout.strategy` actually cost
and buy? The key it replaced ("preset") was silently ignored, so we had never
measured the real always-on layout price. Also doubles as a smoke test that our
production config is accepted by a given engine build.

Usage:
    uv run python load_tests/extractor_ab.py --url http://localhost:18003 \
        --corpus ../benchmark-corpus/arxiv --limit 5
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

import httpx


def default_corpus_dir() -> Path:
    """Where the benchmark PDFs live.

    Kept outside the repo tree and gitignored (~310MB). ``LEMMA_BENCHMARK_CORPUS``
    wins if set; otherwise resolve ``benchmark-corpus/arxiv`` from the repo root,
    which in a git worktree is a symlink to the main checkout so every worktree
    shares one copy.
    """
    override = os.getenv("LEMMA_BENCHMARK_CORPUS")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "benchmark-corpus" / "arxiv"


def build_config(
    strategy: str, *, layout: bool = True, table_model: str = "tatr"
) -> dict:
    """The exact config the backend sends (kreuzberg_helper._build_extract_config)."""
    config: dict = {
        "enable_quality_processing": True,
        "include_document_structure": True,
        "output_format": "markdown",
        "result_format": "unified",
        # Caching is on by default and would turn every repeat measurement into a
        # cache hit, making all variants look identical. Timings are meaningless
        # without this.
        "use_cache": False,
        "pages": {
            "extract_pages": True,
            "insert_page_markers": True,
            "marker_format": "\n\n<!-- PAGE {page_num} -->\n\n",
        },
        "chunking": {"max_chars": 1000, "overlap": 200},
        "ocr": {"backend": "tesseract", "language": "eng"},
        "concurrency": {"max_threads": 4},
        "images": {
            "extract_images": True,
            "target_dpi": 150,
            "include_data_base64": True,
        },
        "pdf_options": {
            "extract_images": True,
            "extract_metadata": True,
            "allow_single_column_tables": True,
        },
    }
    if layout:
        config["pdf_options"]["hierarchy"] = {
            "enabled": True,
            "k_clusters": 6,
            "include_bbox": False,
        }
        config["layout"] = {
            "strategy": strategy,
            "confidence_threshold": 0.5,
            "apply_heuristics": True,
            "table_model": table_model,
        }
    return config


def extract(client: httpx.Client, pdf: Path, config: dict) -> dict:
    started = time.perf_counter()
    response = client.post(
        "/extract",
        files={"files": (pdf.name, pdf.read_bytes(), "application/pdf")},
        data={"config": json.dumps(config)},
    )
    elapsed = time.perf_counter() - started
    out: dict = {"file": pdf.name, "seconds": elapsed, "status": response.status_code}
    if response.status_code >= 400:
        out["error"] = response.text[:300]
        return out

    payload = response.json()
    envelope = isinstance(payload, dict)
    out["envelope"] = envelope
    if envelope:
        results = payload.get("results") or []
        out["errors"] = len(payload.get("errors") or [])
        out["summary"] = payload.get("summary")
    else:
        results = payload
        out["errors"] = 0
    if not results:
        out["error"] = "no results"
        return out

    result = results[0]
    markdown = result.get("content") or ""
    out.update(
        markdown_chars=len(markdown),
        chunks=len(result.get("chunks") or []),
        images=len(result.get("images") or []),
        pages=len(result.get("pages") or []),
        tables=len(result.get("tables") or []),
        headings=sum(1 for line in markdown.splitlines() if line.startswith("#")),
        md_table_rows=markdown.count("\n|"),
        page_markers=markdown.count("<!-- PAGE "),
    )
    return out


def summarize(label: str, rows: list[dict]) -> dict:
    ok = [r for r in rows if r.get("status") == 200 and "error" not in r]
    times = [r["seconds"] for r in ok]
    return {
        "label": label,
        "ok": len(ok),
        "total": len(rows),
        "seconds_total": round(sum(times), 1),
        "seconds_mean": round(statistics.mean(times), 2) if times else None,
        "seconds_max": round(max(times), 2) if times else None,
        "chunks": sum(r.get("chunks", 0) for r in ok),
        "images": sum(r.get("images", 0) for r in ok),
        "tables": sum(r.get("tables", 0) for r in ok),
        "md_table_rows": sum(r.get("md_table_rows", 0) for r in ok),
        "headings": sum(r.get("headings", 0) for r in ok),
        "markdown_chars": sum(r.get("markdown_chars", 0) for r in ok),
        "page_markers": sum(r.get("page_markers", 0) for r in ok),
        "failures": [r for r in rows if r not in ok],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:18003")
    parser.add_argument("--corpus", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    corpus_dir = args.corpus or default_corpus_dir()
    pdfs = sorted(corpus_dir.glob("*.pdf"))[: args.limit]
    if not pdfs:
        raise SystemExit(f"no PDFs in {corpus_dir}")

    variants = {
        "layout_always": build_config("always"),
        "layout_auto": build_config("auto"),
        "no_layout": build_config("auto", layout=False),
    }

    report = {}
    with httpx.Client(base_url=args.url, timeout=httpx.Timeout(args.timeout)) as client:
        # Warm the models first so the first variant isn't charged for the
        # one-time HuggingFace download.
        print(f"warming models on {pdfs[0].name} ...", flush=True)
        warm = extract(client, pdfs[0], build_config("always"))
        print(f"  warm: HTTP {warm['status']} in {warm['seconds']:.1f}s", flush=True)

        for name, config in variants.items():
            rows = []
            for pdf in pdfs:
                row = extract(client, pdf, config)
                rows.append(row)
                note = row.get("error", "")
                print(
                    f"  {name:14s} {pdf.name[:34]:34s} {row['seconds']:6.1f}s "
                    f"HTTP={row['status']} chunks={row.get('chunks', 0):3d} "
                    f"imgs={row.get('images', 0):3d} tbl={row.get('tables', 0):3d} {note[:60]}",
                    flush=True,
                )
            report[name] = summarize(name, rows)

    print("\n=== summary ===")
    for name, data in report.items():
        print(
            f"  {name:14s} ok={data['ok']}/{data['total']} "
            f"total={data['seconds_total']:7.1f}s mean={data['seconds_mean']}s "
            f"chunks={data['chunks']:4d} imgs={data['images']:3d} "
            f"tables={data['tables']:3d} md_rows={data['md_table_rows']:4d} "
            f"headings={data['headings']:3d}"
        )

    always, auto = report.get("layout_always"), report.get("layout_auto")
    if always and auto and always["seconds_total"] and auto["seconds_total"]:
        speedup = always["seconds_total"] / auto["seconds_total"]
        print(f"\n  layout auto vs always: {speedup:.2f}x faster")

    text = json.dumps(report, indent=2, default=str)
    if args.output:
        args.output.write_text(text)
        print(f"  wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
