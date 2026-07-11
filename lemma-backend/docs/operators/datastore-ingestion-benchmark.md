# Datastore ingestion benchmark

This benchmark exercises the real public pipeline: multipart upload, durable
event delivery, worker extraction through Kreuzberg, derived Markdown/images,
local embeddings, PostgreSQL chunk replacement, and terminal file status.

It cycles the checked-in Arxiv PDF fixtures to submit 20 uniquely named files.
OCR is disabled; layout, table and image extraction remain enabled.
Stack readiness includes eager Kreuzberg core-model warming, so concurrent
first uploads cannot race lazy Hugging Face model downloads and silently lose
layout detection.

## Run

From `lemma-backend`:

```bash
make load-test-build
make load-test-datastore-up
make load-test-datastore-setup
make load-test-datastore-files
make load-test-datastore-stats
make load-test-datastore-down
```

The run fails unless every file reaches `COMPLETED`, returns the expected
SHA-256 identity, exposes a generated `document.md`, and the run directory
returns a hybrid search result. Its JSON report defaults to
`/tmp/lemma-datastore-ingestion-report.json` and contains upload latency,
end-to-end ready latency, attempts, phase durations/error, throughput and p95.
Each invocation uses a timestamped child directory, so warm-cache repeats do
not collide with files from earlier runs. Pass `--run-id` directly to the
Python entry point when a stable run label is useful.

## Resource and concurrency matrix

Use the same corpus for every run and clear volumes only when measuring a cold
cache. Useful overrides:

```bash
DATASTORE_KREUZBERG_CPUS=2.0 \
DATASTORE_KREUZBERG_MEMORY=4G \
DATASTORE_WORKER_CPUS=2.0 \
DATASTORE_WORKER_MEMORY=3G \
DATASTORE_EXTRACTION_CONCURRENCY=1 \
DATASTORE_KREUZBERG_TIMEOUT_SECONDS=600 \
make load-test-datastore-up

DATASTORE_BENCH_CONCURRENCY=5 \
DATASTORE_BENCH_TIMEOUT=3600 \
DATASTORE_BENCH_REPORT=/tmp/datastore-2c4g-c1.json \
make load-test-datastore-files
```

Repeat for extraction concurrency 1, 2, 3 and 5 on both 2 CPU/4GB and 4 CPU/8GB.
Capture container CPU/RSS with `make load-test-datastore-stats`. A valid result
requires 20/20 completion, no unhealthy/OOM container, preserved originals and
search-ready chunks. Cold-cache runs include model acquisition; warm-cache runs
measure steady state.

`DATASTORE_BENCH_CONCURRENCY` controls only concurrent HTTP uploads.
`DATASTORE_EXTRACTION_CONCURRENCY` controls the end-to-end processing slots in
each worker process; queued Streaq tasks wait for a slot. The latter limit is
process-local, so multiple worker replicas multiply the deployment-wide
concurrency. A limit of five bounds work at five pipelines but does not make the
memory used by five simultaneous Kreuzberg responses and embedding/indexing
buffers fit a smaller host.

The defaults allocate 4 CPU/8GB to Kreuzberg and 4 CPU/4GB to the backend
worker. Report both budgets: extraction and local embedding consume different
containers, and constraining either one changes end-to-end throughput.
The regular development Compose stack now uses the same 4 CPU/8GB Kreuzberg
target by default; override `DEV_KREUZBERG_CPUS` and
`DEV_KREUZBERG_MEM_LIMIT` together when using a smaller Docker VM.
Docker Desktop must therefore have substantially more than 12GB assigned once
PostgreSQL, Redis, SuperTokens and the API are included. On an 8GB Docker VM,
use the documented 2 CPU/4GB Kreuzberg and 2 CPU/3GB worker profile; otherwise
the kernel can OOM-kill a healthy extractor even though its own limit says 8GB.

## Verified local baseline

On 2026-07-11, the full checked-in corpus passed on an 8GB Docker Desktop VM
with five concurrent uploads, one end-to-end processing slot, Kreuzberg at
2 CPU/4GB, and the worker at 4 CPU/3GB:

- 20/20 `COMPLETED`; 20 canonical originals and SHA-256 identities
- 20/20 generated `document.md`; hybrid search returned results
- 2,186 indexed chunks across all 20 files
- 26m59s wall time (0.74 documents/minute)
- extraction p95 66.6s; indexing p95 170.8s
- end-to-end p95 23m05s, including intentional queue wait
- zero API/worker/Kreuzberg restarts or OOMs

That cold run exposed an incomplete FastEmbed snapshot in the API process after
the worker had already populated the shared model cache. FastEmbed now owns the
alternate download, validation, placement and reuse in its standard cache; the
adapter only selects FastEmbed's registered alternate when an incomplete Hub
snapshot fails during ONNX session construction. Both API and worker preload
the process-wide singleton before readiness. A fresh process subsequently
loaded the cached 768-dimensional model in 1.1 seconds without a download.

The regular development Kreuzberg container was also exercised sequentially at
4 CPU/8GB over all six checked-in digital-first PDFs. It completed 148 pages in
133.9 seconds total: five 12-16-page papers took 12.9-17.9 seconds each, while a
75-page paper with 166 extracted images took 59.3 seconds. Peak sampling showed
about 401% CPU and 3.33GB RSS with no restart or OOM.

The 4 CPU/8GB Kreuzberg target profile could not be measured on that VM: the
entire Docker VM, not the Kreuzberg container, was capped at 7.75GB. Docker
recorded exit 137/OOM when the extractor and real FastEmbed worker peaked
together. Run that target only with enough host memory for both budgets plus
the API and infrastructure services.
