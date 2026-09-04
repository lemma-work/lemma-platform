"""Benchmark document-processor adapters on the arxiv research PDFs.

Runs a ``DocumentProcessorPort`` adapter over
``app/modules/datastore/tests/fixtures/arxiv/*.pdf``, writes the produced
markdown (+ extracted images) per document, and prints per-doc wall-clock time
and structure counts — so we can eyeball markdown quality and confirm the fast
Kreuzberg path lands ~10-20s per digital-first PDF.

Usage (from lemma-backend/):
  uv run python scripts/experiments/bench_document_processors.py \
      --processor xberg --out /tmp/docproc

  # Kreuzberg/Xberg needs the service running. The dev compose stack does NOT
  # include it (it runs the in-process xberg adapter), so start one
  # directly. Both engines speak the same adapter:
  docker run -d -p 8002:8000 ghcr.io/kreuzberg-dev/kreuzberg-core:4.10.2 \
      serve --host 0.0.0.0 --port 8000
  #   ...or the current line:
  #   ghcr.io/xberg-io/xberg:1.0.14-core serve --host 0.0.0.0 --port 8000
  KREUZBERG_URL=http://localhost:8002 uv run python \
      scripts/experiments/bench_document_processors.py --processor kreuzberg --out /tmp/docproc

For an engine-vs-engine comparison on a real corpus, prefer
``load_tests/extractor_ab.py`` — it drives the exact config the backend sends and
disables the extractor's result cache, which otherwise makes repeat runs
meaningless.
"""

from __future__ import annotations

import argparse
import asyncio
import resource
import sys
import time
from pathlib import Path

# Make ``app`` importable when run as a bare script.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.modules.datastore.domain.ports import DocumentProcessorPort  # noqa: E402

FIXTURES = (
    Path(__file__).resolve().parents[2] / "app/modules/datastore/tests/fixtures/arxiv"
)


def _make_processor(name: str) -> DocumentProcessorPort:
    if name == "xberg":
        from app.modules.datastore.infrastructure.xberg_processor import (
            XbergDocumentProcessor,
        )

        return XbergDocumentProcessor()
    if name == "kreuzberg":
        from app.modules.datastore.infrastructure.document_processor import (
            KreuzbergDocumentProcessor,
        )

        return KreuzbergDocumentProcessor()
    if name == "docling":
        from app.modules.datastore.infrastructure.docling_processor import (
            DoclingDocumentProcessor,
        )

        return DoclingDocumentProcessor()
    raise SystemExit(f"unknown processor: {name}")


def _peak_rss_mb() -> float:
    # ru_maxrss is bytes on macOS, kilobytes on Linux.
    maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return maxrss / (1024 * 1024) if sys.platform == "darwin" else maxrss / 1024


def _write_output(out_dir: Path, extraction) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "document.md").write_text(extraction.markdown, encoding="utf-8")
    for image in extraction.images:
        (out_dir / image.name).write_bytes(image.content)


async def _run(processor_name: str, out_root: Path | None) -> None:
    processor = _make_processor(processor_name)
    pdfs = sorted(FIXTURES.glob("*.pdf"))
    if not pdfs:
        raise SystemExit(f"no fixtures found in {FIXTURES}")

    print(f"processor={processor_name}  docs={len(pdfs)}")
    header = f"{'doc':<28}{'secs':>8}{'pages':>7}{'tables':>7}{'images':>7}{'chunks':>7}{'md_kb':>8}"
    print(header)
    print("-" * len(header))
    total = 0.0
    for pdf in pdfs:
        content = pdf.read_bytes()
        start = time.perf_counter()
        extraction = await processor.extract(
            content, pdf.name, mime_type="application/pdf"
        )
        secs = time.perf_counter() - start
        total += secs
        tables = sum(page.table_count for page in extraction.pages)
        if out_root is not None:
            _write_output(out_root / processor_name / pdf.stem, extraction)
        print(
            f"{pdf.name:<28}{secs:>8.1f}{extraction.page_count:>7}"
            f"{tables:>7}{len(extraction.images):>7}{len(extraction.chunks):>7}"
            f"{len(extraction.markdown) / 1024:>8.1f}"
        )
    print("-" * len(header))
    print(f"{'TOTAL':<28}{total:>8.1f}")
    # In-process peak RSS is meaningful for xberg; for docling/kreuzberg the
    # heavy work runs in their container (measure that with `docker stats`).
    print(f"in-process peak RSS: {_peak_rss_mb():.0f} MB")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--processor",
        default="xberg",
        choices=["xberg", "kreuzberg", "docling"],
        help="which adapter to benchmark",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="directory to write produced markdown/images (omit to skip writing)",
    )
    args = parser.parse_args()
    out_root = Path(args.out) if args.out else None
    asyncio.run(_run(args.processor, out_root))


if __name__ == "__main__":
    main()
