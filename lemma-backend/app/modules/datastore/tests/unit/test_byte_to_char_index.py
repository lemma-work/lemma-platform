def _reference(content: str, byte_offsets: list[int]) -> dict[int, int]:
    """The per-character version this replaced, kept as the oracle."""
    targets = sorted(set(byte_offsets))
    mapping: dict[int, int] = {}
    cursor = 0
    nbytes = 0
    for char_index, char in enumerate(content):
        while cursor < len(targets) and targets[cursor] <= nbytes:
            mapping[targets[cursor]] = char_index
            cursor += 1
        nbytes += len(char.encode("utf-8"))
    while cursor < len(targets):
        mapping[targets[cursor]] = len(content)
        cursor += 1
    return mapping


def test_byte_to_char_index_matches_the_per_character_version():
    """Same answers, without encoding every character to get them.

    The replaced version called ``char.encode("utf-8")`` once per character of
    the whole document — on the event loop — to answer a question about a
    handful of page boundaries. Correctness is checked against it directly,
    including the cases that make UTF-8 offsets interesting: multi-byte
    characters, a boundary landing inside one, and offsets past the end.
    """
    from app.modules.datastore.infrastructure.document_processor import (
        KreuzbergDocumentProcessor,
    )

    samples = [
        "plain ascii text only",
        "naïve café — em dash and accents",
        "emoji 👍 four byte 𝄞 mixed with ascii",
        "",
        "ünïcödé" * 50,
    ]
    for content in samples:
        data = content.encode("utf-8")
        offsets = sorted(
            {0, 1, 2, 3, len(data) // 2, max(0, len(data) - 1), len(data), len(data) + 25}
        )
        assert KreuzbergDocumentProcessor._byte_to_char_index(
            content, offsets
        ) == _reference(content, offsets), content[:20]
