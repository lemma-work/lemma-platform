"""Render the live FastAPI schema as the public client spec, offline.

Both SDKs are generated *from* ``lemma-python/lemma_sdk/openapi_spec.json``, and
CI's codegen-drift job regenerates them from it and fails if the committed
clients disagree. But nothing checked that file against the code: it is only
rewritten by ``generate_openapi_client.sh``, which needs a running server to
fetch ``/openapi.json``. So a new route could be added, committed, and pass CI
while both SDKs silently lacked it - the drift gate only ever compared the
clients to a spec that may already have been stale.

Importing the app is enough: ``app.openapi()`` runs the same ``custom_openapi``
that injects the ``x-lemma`` metadata blocks, so this produces exactly what a
regen against a running server would, with no database or network. The result is
then pruned through ``prepare_client_openapi`` with the shared exclusion list,
because the committed artifact is the *client* spec - billing, the scheduler and
the inbound webhook receivers are deliberately absent from it.

Write a spec to feed the generators (they write the committed artifact, and
their API-version guard depends on still seeing the old one)::

    uv run python scripts/dump_openapi_spec.py
    cd ../lemma-python && OPENAPI_FILE=../lemma-backend/.generated/openapi.json \\
        bash scripts/generate_openapi_client.sh

Or check the committed spec against the code, which is what CI wants::

    uv run python scripts/dump_openapi_spec.py --check
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = REPO_ROOT / "lemma-backend/.generated/openapi.json"
COMMITTED_SPEC = REPO_ROOT / "lemma-python/lemma_sdk/openapi_spec.json"

sys.path.insert(0, str(REPO_ROOT / "scripts"))


def render() -> str:
    # Imported lazily so --help works without loading the whole app.
    from app.app import app
    from prepare_client_openapi import (
        DEFAULT_EXCLUDED_PREFIXES,
        DEFAULT_EXCLUDED_TAGS,
        prepare_client_openapi,
    )

    schema = prepare_client_openapi(
        json.loads(json.dumps(app.openapi())),
        excluded_tags={tag.lower() for tag in DEFAULT_EXCLUDED_TAGS},
        excluded_prefixes=DEFAULT_EXCLUDED_PREFIXES,
        prune_unreferenced_schemas=True,
    )
    # Match the normalization the generators apply, so a dump and a regenerate
    # produce byte-identical files.
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "compare the committed SDK spec against the live app and fail if it "
            "is stale, instead of writing anything"
        ),
    )
    arguments = parser.parse_args()

    rendered = render()
    if arguments.check:
        committed = (
            COMMITTED_SPEC.read_text(encoding="utf-8")
            if COMMITTED_SPEC.exists()
            else ""
        )
        if committed != rendered:
            print(
                f"{COMMITTED_SPEC} does not match the routes this app defines. "
                "Regenerate the SDKs (see lemma-python/scripts/"
                "generate_openapi_client.sh) and commit the result.",
                file=sys.stderr,
            )
            return 1
        print(f"OpenAPI spec is current: {COMMITTED_SPEC}")
        return 0

    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(rendered, encoding="utf-8")
    print(f"Wrote {arguments.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
