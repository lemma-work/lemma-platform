"""Single source of truth for the public API (OpenAPI) version.

This value is surfaced as the FastAPI ``version`` on every app variant
(``app.app:create_app``, ``standalone_app``) and therefore as
``info.version`` in ``GET /openapi.json``. The generated ``lemma-sdk``
records the version it was built against (``lemma_sdk._spec_info``), and the
``lemma`` CLI (``lemma --version`` / ``lemma doctor``) compares the two to flag
client/server skew.

Move this when a *release* changes the public API, not on every branch that
touches the schema. Regeneration used to refuse a schema change that left this
value alone, which meant an unreleased number climbed once per PR and every
schema-touching branch conflicted with every other on this one line — for a
version naming a release that had not happened.

Skew detection does not rely on this label: ``lemma_sdk._spec_info.SPEC_SHA256``
fingerprints the schema itself, so ``lemma doctor`` reports a mismatched client
regardless of what the version string says. This value is what a release is
*called*, and every component must agree on it at release time —
``scripts/check_version_consistency.py`` is run by the release workflows against
the tag and blocks publishing when anything disagrees.

During beta, compatible API changes bump the patch and breaking API changes bump
the shared API/SDK minor compatibility line. After 1.0, breaking changes bump the
shared major compatibility line.

Use a normal MAJOR.MINOR.PATCH string.
"""

from __future__ import annotations

API_VERSION = "0.7.2"
