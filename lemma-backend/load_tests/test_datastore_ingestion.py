from pathlib import Path

import pytest

from load_tests.datastore_ingestion import _corpus, _percentile, _timing_summary


def test_corpus_cycles_sorted_pdf_fixtures(tmp_path: Path):
    (tmp_path / "b.pdf").write_bytes(b"b")
    (tmp_path / "a.pdf").write_bytes(b"a")

    assert [item.name for item in _corpus(tmp_path, 5)] == [
        "a.pdf",
        "b.pdf",
        "a.pdf",
        "b.pdf",
        "a.pdf",
    ]


def test_corpus_requires_pdf_fixture(tmp_path: Path):
    with pytest.raises(ValueError, match="No PDF fixtures"):
        _corpus(tmp_path, 20)


def test_percentile_interpolates_small_samples():
    assert _percentile([1.0, 2.0, 3.0, 4.0], 0.5) == 2.5
    assert _percentile([], 0.95) is None


def test_timing_summary_reports_empty_and_populated_samples():
    assert _timing_summary([]) == {
        "mean_seconds": None,
        "p50_seconds": None,
        "p95_seconds": None,
        "max_seconds": None,
    }
    assert _timing_summary([1.0, 2.0, 3.0])["p95_seconds"] == pytest.approx(2.9)
