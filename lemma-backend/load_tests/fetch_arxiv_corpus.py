#!/usr/bin/env python3
"""Download a corpus of significant LLM papers from arXiv for load testing.

Why real papers and not synthetic PDFs: extraction cost is driven by page count,
column layout, table density and figure count, and synthetic documents have none
of that variety. A 100-paper arXiv corpus spans ~4 to ~100 pages, single and
double column, heavy tables and heavy figures — which is what makes throughput
and peak-RSS numbers from a load test mean anything.

Usage:
    uv run python load_tests/fetch_arxiv_corpus.py [--limit 100] [--out DIR]

Downloads are skipped when the file already exists, so re-running is cheap.
arXiv asks for a few seconds between requests; the default delay respects that.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# arXiv ids of widely-cited LLM / deep-learning papers, chosen to span a range
# of lengths and layouts rather than to be a "top N" ranking.
PAPERS: list[tuple[str, str]] = [
    ("1706.03762", "attention_is_all_you_need"),
    ("1810.04805", "bert"),
    ("2005.14165", "gpt3_few_shot_learners"),
    ("1907.11692", "roberta"),
    ("1910.10683", "t5_exploring_transfer_learning"),
    ("1910.01108", "distilbert"),
    ("2001.08361", "scaling_laws_neural_lm"),
    ("2203.15556", "chinchilla_compute_optimal"),
    ("2302.13971", "llama"),
    ("2307.09288", "llama2"),
    ("2407.21783", "llama3_herd"),
    ("2303.08774", "gpt4_technical_report"),
    ("2204.02311", "palm"),
    ("2112.11446", "gopher"),
    ("2201.11903", "chain_of_thought"),
    ("2203.11171", "self_consistency"),
    ("2205.11916", "llms_are_zero_shot_reasoners"),
    ("2210.03629", "react_reasoning_acting"),
    ("2305.10601", "tree_of_thoughts"),
    ("2203.02155", "instructgpt"),
    ("2212.08073", "constitutional_ai"),
    ("2204.05862", "hh_rlhf"),
    ("2305.18290", "dpo_direct_preference"),
    ("1707.06347", "ppo"),
    ("2106.09685", "lora"),
    ("2305.14314", "qlora"),
    ("2104.08691", "prompt_tuning"),
    ("2101.00190", "prefix_tuning"),
    ("1902.00751", "adapter_houlsby"),
    ("2110.08207", "t0_multitask_prompted"),
    ("2109.01652", "flan_finetuned_lms"),
    ("2210.11416", "scaling_instruction_finetuned"),
    ("2005.11401", "rag"),
    ("2004.04906", "dpr_dense_passage_retrieval"),
    ("2007.01282", "fid_fusion_in_decoder"),
    ("2112.04426", "retro_retrieval_enhanced"),
    ("2208.03299", "atlas_few_shot_retrieval"),
    ("2312.10997", "rag_survey"),
    ("1908.10084", "sentence_bert"),
    ("2212.03533", "e5_text_embeddings"),
    ("2104.08821", "simcse"),
    ("2205.14135", "flashattention"),
    ("2307.08691", "flashattention2"),
    ("1909.08053", "megatron_lm"),
    ("1910.02054", "zero_memory_optimization"),
    ("2104.04473", "efficient_large_scale_training"),
    ("2101.03961", "switch_transformers"),
    ("2006.16668", "gshard"),
    ("2401.04088", "mixtral_of_experts"),
    ("2211.05100", "bloom"),
    ("2205.01068", "opt_open_pretrained"),
    ("2210.02414", "glm_130b"),
    ("2309.16609", "qwen"),
    ("2310.06825", "mistral_7b"),
    ("2403.05530", "gemini_1_5"),
    ("2312.11805", "gemini"),
    ("2305.10403", "palm2"),
    ("2108.07258", "foundation_models_opportunities"),
    ("2206.07682", "emergent_abilities"),
    ("2206.04615", "big_bench"),
    ("2009.03300", "mmlu_measuring_massive"),
    ("2110.14168", "gsm8k_math_word_problems"),
    ("2107.03374", "codex_evaluating_llms_code"),
    ("2308.12950", "code_llama"),
    ("2211.10435", "pal_program_aided"),
    ("2303.12712", "sparks_of_agi"),
    ("2303.17580", "hugginggpt"),
    ("2302.04761", "toolformer"),
    ("2304.03442", "generative_agents"),
    ("2308.08155", "autogen"),
    ("2303.11366", "reflexion"),
    ("2305.16291", "voyager"),
    ("2201.08239", "lamda"),
    ("2112.09332", "webgpt"),
    ("2009.01325", "learning_to_summarize_hf"),
    ("1804.07461", "glue"),
    ("1905.00537", "superglue"),
    ("1606.05250", "squad"),
    ("2109.07958", "truthfulqa"),
    ("2009.11462", "realtoxicityprompts"),
    ("2202.03286", "red_teaming_lms"),
    ("2308.03688", "agentbench"),
    ("2306.05685", "judging_llm_as_a_judge"),
    ("2211.09110", "helm_holistic_evaluation"),
    ("1503.02531", "distilling_knowledge"),
    ("1810.03993", "model_cards"),
    ("1803.09010", "datasheets_for_datasets"),
    ("2104.09864", "rope_rotary_embeddings"),
    ("2108.12409", "alibi_short_train_long_test"),
    ("1607.06450", "layer_normalization"),
    ("2002.05202", "glu_variants_transformer"),
    ("1910.07467", "rmsnorm"),
    ("2302.13971v1", "llama_v1_alt"),
    ("1409.0473", "neural_mt_jointly_align"),
    ("1409.3215", "seq2seq"),
    ("1512.03385", "resnet"),
    ("2010.11929", "vit_image_is_worth_16x16"),
    ("2103.00020", "clip"),
    ("2304.08485", "llava_visual_instruction"),
    ("2301.12597", "blip2"),
    ("1412.6980", "adam_optimizer"),
    ("1207.0580", "dropout_preventing_coadaptation"),
]

ARXIV_PDF_URL = "https://arxiv.org/pdf/{arxiv_id}"
USER_AGENT = "lemma-load-test/1.0 (document-processing benchmark; contact: ops@lemma.local)"



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


def download(arxiv_id: str, slug: str, out_dir: Path, delay: float) -> tuple[str, int, str]:
    target = out_dir / f"{slug}.pdf"
    if target.exists() and target.stat().st_size > 10_000:
        return slug, target.stat().st_size, "cached"

    url = ARXIV_PDF_URL.format(arxiv_id=arxiv_id)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            payload = response.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        return slug, 0, f"failed: {type(exc).__name__}"

    # arXiv serves an HTML interstitial for withdrawn/missing ids; a PDF starts %PDF.
    if not payload.startswith(b"%PDF"):
        return slug, 0, "failed: not a pdf"

    target.write_bytes(payload)
    time.sleep(delay)
    return slug, len(payload), "downloaded"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
    )
    parser.add_argument("--delay", type=float, default=3.0, help="seconds between downloads")
    args = parser.parse_args()

    out_dir: Path = args.out or default_corpus_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    selected = PAPERS[: args.limit]
    results = []
    total_bytes = 0
    failures = []
    for index, (arxiv_id, slug) in enumerate(selected, start=1):
        slug, size, status = download(arxiv_id, slug, out_dir, args.delay)
        results.append({"id": arxiv_id, "slug": slug, "bytes": size, "status": status})
        total_bytes += size
        if status.startswith("failed"):
            failures.append((arxiv_id, slug, status))
        print(
            f"[{index:3d}/{len(selected)}] {status:12s} {slug:38s} {size / 1e6:6.2f} MB",
            flush=True,
        )

    manifest = out_dir / "manifest.json"
    manifest.write_text(json.dumps(results, indent=2))

    ok = [r for r in results if not r["status"].startswith("failed")]
    print(f"\ncorpus: {len(ok)}/{len(selected)} papers, {total_bytes / 1e6:.1f} MB total")
    print(f"manifest: {manifest}")
    if failures:
        print(f"\n{len(failures)} failed:")
        for arxiv_id, slug, status in failures:
            print(f"  {arxiv_id:14s} {slug:38s} {status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
