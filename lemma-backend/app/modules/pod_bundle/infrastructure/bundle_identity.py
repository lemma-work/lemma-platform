"""Which export a bundle came out of, carried inside the bundle.

An export and every import of what it produced are separate jobs with separate
ids, and nothing else connects them: the archive travels by download, upload or
shared link, and the import sees only the archive. So the export writes its own
id into ``pod.json`` and the import reads it back. That is the whole of how
``bundle.exported`` and ``import.completed`` name the same bundle -- each used to
send its own job id, which made "imports per bundle shared" uncountable.

Untrusted on the way back in: a bundle is a file anyone can edit, and one built
by the CLI or written by hand carries no id at all. A value that is not a UUID
is no identity rather than an error, so it can never fail an import.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from uuid import UUID

from lemma_pod_bundle.layout import POD_MANIFEST_FILE, _read_json

BUNDLE_ID_KEY = "bundle_id"


def stamp_bundle_id(
    pod_payload: Mapping[str, object], bundle_id: UUID
) -> dict[str, object]:
    """``pod.json``'s payload, naming the export that is writing it."""
    return {**pod_payload, BUNDLE_ID_KEY: str(bundle_id)}


def read_bundle_id(bundle_root: Path) -> UUID | None:
    """The export this extracted bundle came from, if it says and says validly."""
    pod_path = bundle_root / POD_MANIFEST_FILE
    if not pod_path.is_file():
        return None
    try:
        raw = _read_json(pod_path).get(BUNDLE_ID_KEY)
    except ValueError, OSError:
        return None
    if not isinstance(raw, str):
        return None
    try:
        return UUID(raw)
    except ValueError:
        return None


__all__ = ["BUNDLE_ID_KEY", "read_bundle_id", "stamp_bundle_id"]
