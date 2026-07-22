"""Image pulls for the two app services, infra, and sandbox runtime image."""

from __future__ import annotations

from lemma_stack.output import info
from lemma_stack.release.manifest import ReleaseManifest
from lemma_stack.runtime.base import Runtime


def pull_release(
    runtime: Runtime,
    manifest: ReleaseManifest,
    *,
    skip_existing: bool = True,
) -> None:
    for ref in manifest.all_pull_refs():
        if skip_existing and runtime.image_exists(ref):
            info(f"image present: {ref}")
            continue
        info(f"pulling {ref}")
        runtime.pull(ref)
