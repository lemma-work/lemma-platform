"""AdminContext: everything a command needs about the installed stack."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

from tomlkit import TOMLDocument

from lemma_stack.config import store
from lemma_stack.paths import LocalPaths
from lemma_stack.release import manifest as release_manifest
from lemma_stack.release.manifest import ReleaseManifest
from lemma_stack.runtime import detect
from lemma_stack.runtime.base import Runtime
from lemma_stack.stack import specs as specs_mod
from lemma_stack.stack.specs import ServiceSpec


@dataclass
class AdminContext:
    paths: LocalPaths
    config: TOMLDocument

    @classmethod
    def load(cls) -> "AdminContext":
        paths = LocalPaths()
        return cls(paths=paths, config=store.load(paths))

    @property
    def provider(self) -> str:
        return store.provider(self.config)

    @cached_property
    def runtime(self) -> Runtime:
        return detect.ensure_ready(self.provider)

    @cached_property
    def manifest(self) -> ReleaseManifest:
        return release_manifest.load_pinned(self.paths)

    def specs(
        self,
        manifest: ReleaseManifest | None = None,
        *,
        host_apps: bool = False,
    ) -> list[ServiceSpec]:
        return specs_mod.build_specs(
            self.config,
            self.paths,
            manifest or self.manifest,
            provider=self.provider,
            host_socket=self.runtime.socket_path(),
            host_apps=host_apps,
        )

    def save_config(self) -> None:
        store.save(self.paths, self.config)
