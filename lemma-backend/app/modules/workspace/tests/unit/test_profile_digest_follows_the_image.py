"""A sandbox is replaced when the image behind it changes.

`_is_stale` compares a sandbox's recorded profile digest against what is
configured now, and replaces the sandbox when they differ. That is the only
lever that reaches a sandbox which already exists -- so if the configured digest
does not move when the image does, a release ships a new workspace image and
every existing sandbox keeps running the old one.

It did not move. The setting is a hand-bumped placeholder whose own comment says
it was "last moved when the GitHub CLI was added to the image", so every image
change after that reached only sandboxes created afterwards. The fix is to stop
depending on anybody remembering: a pinned image reference already carries its
digest, and desktop and local installs resolve exactly such a reference from the
release manifest.
"""

from __future__ import annotations

from app.modules.workspace.config import workspace_settings
from app.modules.workspace.providers.profiles import (
    _digest_for,
    function_profile,
    workspace_profile,
)

# What a desktop or local install actually resolves, copied from a real
# `host-pack.json`: the tag names the build and the digest pins the content.
_PINNED = (
    "ghcr.io/lemma-work/lemma-workspace:test-f3e6907c986e"
    "@sha256:d9ca51423899f20f527873f81fcfe9b9aec364d796e6f5fc895c0024133932cc"
)
_CONFIGURED = f"sha256:{'3' * 64}"


def test_a_pinned_image_is_its_own_profile_digest():
    assert _digest_for(_PINNED, _CONFIGURED) == (
        "sha256:d9ca51423899f20f527873f81fcfe9b9aec364d796e6f5fc895c0024133932cc"
    )


def test_two_releases_of_the_same_tag_do_not_compare_equal():
    """The actual failure: a new image under an unchanged tag.

    Every nightly and every release publishes `lemma-workspace` afresh. If the
    profile digest tracked the tag, or a constant, an existing sandbox would
    compare equal to the new release and never be rebuilt -- which is exactly
    how a sandbox ends up running an image the backend no longer matches.
    """
    older = f"ghcr.io/lemma-work/lemma-workspace:latest@sha256:{'a' * 64}"
    newer = f"ghcr.io/lemma-work/lemma-workspace:latest@sha256:{'b' * 64}"

    assert _digest_for(older, _CONFIGURED) != _digest_for(newer, _CONFIGURED)


def test_a_floating_tag_still_uses_the_configured_digest():
    """`lemma-workspace:dev` carries no digest, and a developer rebuilding the
    same tag does not want their whole fleet replaced on every build."""
    assert _digest_for("lemma-workspace:dev", _CONFIGURED) == _CONFIGURED


def test_both_kinds_derive_the_same_way(monkeypatch):
    """Function sandboxes are pinned from the same manifest and were equally
    stale; the fix would be half-done if only the workspace kind followed."""
    monkeypatch.setattr(workspace_settings, "workspace_image", _PINNED, raising=False)
    monkeypatch.setattr(
        workspace_settings,
        "function_image",
        f"ghcr.io/lemma-work/lemma-function:v1@sha256:{'c' * 64}",
        raising=False,
    )

    assert workspace_profile().digest.endswith("133932cc")
    assert function_profile().digest == f"sha256:{'c' * 64}"
