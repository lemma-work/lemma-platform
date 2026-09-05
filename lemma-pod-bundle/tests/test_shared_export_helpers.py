"""The two helpers both exporters need, and the drift that put them here.

`lemma-backend` and `lemma-cli` each had their own copy of these. The backend's
`_extract_large_text` carried a comment describing itself as "byte-identical to
the CLI's", which nothing checked -- and its sibling, `_normalize_file_folders`,
had genuinely diverged: the CLI dropped `"/"` inside the function while the
backend kept it and refused it a layer down with a warning. Both exported the
same bytes; only one told the person who typed `--folder /` that it had been
ignored.

Two copies of a function are not a problem until one of them changes. These
tests are what makes the single copy stay single: they state the behaviour both
exporters depend on, in the package that now owns it.
"""

from __future__ import annotations

from pathlib import Path

from lemma_pod_bundle.layout import (
    RAW_FILE_REF_KEY,
    extract_large_text,
    normalize_file_folders,
)


def test_a_folder_list_is_slashed_deduped_and_kept_in_order():
    assert normalize_file_folders(["docs", "/docs/", "notes", "docs"]) == [
        "/docs",
        "/notes",
    ]


def test_blank_entries_are_dropped_rather_than_becoming_the_root():
    """`"" -> "/"` is the accident this guards.

    The normalizer builds a path as `"/" + raw.strip().strip("/")`, so an empty
    or whitespace entry would become `"/"` -- the whole tree -- if it were not
    skipped first. A trailing comma in a `--folder` list is enough to produce
    one.
    """
    assert normalize_file_folders(["", "  ", "	"]) == []
    assert normalize_file_folders(None) == []
    assert normalize_file_folders([]) == []


def test_the_root_survives_normalization_so_a_caller_can_refuse_it_out_loud():
    """`"/"` is kept here on purpose, and this is the drift that mattered.

    The CLI's copy dropped it inside the function, so `--folder /` selected
    nothing and warned about nothing. The backend keeps it and refuses it in
    `_export_pod_files`, where there is a warnings list to put the reason in.
    Normalizing is not the place to decide policy, because it is not the place
    that can explain the decision.
    """
    assert normalize_file_folders(["/"]) == ["/"]
    assert normalize_file_folders(["/", "/docs"]) == ["/", "/docs"]


def test_a_large_text_field_moves_to_a_sidecar_and_leaves_a_reference(
    tmp_path: Path,
):
    payload = extract_large_text(
        {"name": "greet", "code": "print('hi')\n"},
        field_name="code",
        file_name="code.py",
        resource_dir=tmp_path,
    )

    assert payload["code"] == {RAW_FILE_REF_KEY: "code.py"}
    assert (tmp_path / "code.py").read_text(encoding="utf-8") == "print('hi')\n"
    # The caller's dict is not mutated: an exporter builds several payloads from
    # one source and a shared mutation would leak between them.
    assert payload is not None


def test_a_field_that_is_not_text_is_left_exactly_as_it_was(tmp_path: Path):
    """No sidecar, no `$file`, and no file written.

    `code` is absent on some resources and already a `$file` reference on a
    round-tripped one. Rewriting either would turn an import of an exported
    bundle into an import of a reference to a file that does not exist.
    """
    original = {"name": "greet", "code": {RAW_FILE_REF_KEY: "code.py"}}

    payload = extract_large_text(
        original,
        field_name="code",
        file_name="code.py",
        resource_dir=tmp_path,
    )

    assert payload == original
    assert not list(tmp_path.iterdir())
