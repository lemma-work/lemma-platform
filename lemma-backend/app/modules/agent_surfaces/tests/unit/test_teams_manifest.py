"""The Teams app manifest changes only with its version.

Teams installs an app once per tenant and offers an update only when the
manifest's ``version`` rises; a manifest whose permissions changed under the
same version is never picked up by a tenant that has the old one, and an admin
is never asked to consent to what it now reads. So the manifest's content is
pinned per version: changing it means bumping ``version`` and adding its
digest here. The packaged zip (``make teams-manifest-zip``) carries the same
manifest.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

MANIFEST_DIR = Path(__file__).resolve().parents[5] / "manifests" / "teams"

#: Each released manifest version, and the digest of everything else in it.
CONTENT_BY_VERSION = {
    # RSC: ChannelMessage.Read.Group and ChatMessage.Read.Chat, so channel and
    # group-chat talk reaches the bot without an @mention.
    "1.1.0": "805bfbb5f41601fb2d212bda3738ddb87511fbb228fd1126b89eca175f4f016e",
    # RSC: ChannelSettings.Read.Group, so the bot can ask Graph whether a team
    # channel is standard (open to the team) or private before mirroring it.
    "1.2.0": "963df5118f1cb393be2742468aea32c5cdac47ada0ebd5c867c615e74067ef20",
}


def _digest(manifest: dict[str, object]) -> str:
    content = {key: value for key, value in manifest.items() if key != "version"}
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _manifest() -> dict[str, object]:
    return json.loads((MANIFEST_DIR / "manifest.json").read_text())


def test_the_manifest_changes_only_with_its_version():
    manifest = _manifest()
    version = manifest["version"]
    assert version in CONTENT_BY_VERSION, (
        f"Teams manifest version {version} is not pinned: add its digest"
    )
    assert _digest(manifest) == CONTENT_BY_VERSION[version], (
        "The Teams manifest changed without a version bump: raise `version` "
        "and pin the new digest, or tenants never see the change"
    )


def test_the_manifest_reads_the_messages_channels_log():
    permissions = _manifest()["authorization"]["permissions"]["resourceSpecific"]
    assert {permission["name"] for permission in permissions} >= {
        "ChannelMessage.Read.Group",
        "ChatMessage.Read.Chat",
        "ChannelSettings.Read.Group",
    }


def test_the_packaged_app_is_the_manifest():
    with zipfile.ZipFile(MANIFEST_DIR / "lemma-teams.zip") as package:
        packaged = json.loads(package.read("manifest.json"))
        assert set(package.namelist()) == {"manifest.json", "color.png", "outline.png"}
    assert packaged == _manifest(), "Run `make teams-manifest-zip`"
