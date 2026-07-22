"""Image pulls for the two app services, infra, and sandbox runtime image."""

from __future__ import annotations

from lemma_stack.output import info
from lemma_stack.release.manifest import ReleaseManifest
from lemma_stack.runtime.base import Runtime


def pull_release(
    runtime: Runtime,
    manifest: ReleaseManifest,
    *,
    infra_only: bool = False,
    skip_existing: bool = True,
) -> None:
    refs = manifest.infra_pull_refs() if infra_only else manifest.all_pull_refs()
    for ref in refs:
        if skip_existing and runtime.image_exists(ref):
            info(f"image present: {ref}")
            continue
        info(f"pulling {ref}")
        runtime.pull(ref)
