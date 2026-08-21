from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import keyword
import os
from pathlib import Path
import shutil
import tempfile
from uuid import UUID
import zipfile

from app.core.config import settings
from app.modules.function.domain.entities import (
    FunctionArtifact,
    FunctionArtifactManifest,
)
from app.modules.function.domain.errors import FunctionValidationError
from app.modules.function.domain.ports import FunctionStorageFactoryPort
from app.core.concurrency.offload import run_blocking


FUNCTION_PYTHON_VERSION = "3.14"
FUNCTION_PYTHON_PLATFORM = "x86_64-manylinux_2_28"
RUNTIME_ABI = "lemma-function-python-3.14-linux-x86_64-1"


@dataclass(frozen=True, slots=True)
class FunctionRuntimeHeader:
    input_model: str
    output_model: str
    entrypoint: str
    config_model: str | None


def parse_runtime_header(code: str) -> FunctionRuntimeHeader:
    headers: dict[str, str] = {}
    for line in code.splitlines()[:8]:
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("#") or ":" not in stripped:
            break
        name, value = stripped[1:].split(":", 1)
        headers[name.strip()] = value.strip()
    required = {
        "input_type_name": headers.get("input_type_name"),
        "output_type_name": headers.get("output_type_name"),
        "function_name": headers.get("function_name"),
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise FunctionValidationError(
            f"Missing function code header(s): {', '.join(missing)}"
        )
    identifiers = {
        "input_type_name": required["input_type_name"],
        "output_type_name": required["output_type_name"],
        "function_name": required["function_name"],
        "config_type_name": headers.get("config_type_name"),
    }
    invalid = [
        name
        for name, value in identifiers.items()
        if value and (not value.isidentifier() or keyword.iskeyword(value))
    ]
    if invalid:
        raise FunctionValidationError(
            "Function code headers must contain plain Python identifiers; "
            f"invalid header(s): {', '.join(invalid)}"
        )
    return FunctionRuntimeHeader(
        input_model=required["input_type_name"] or "",
        output_model=required["output_type_name"] or "",
        entrypoint=required["function_name"] or "",
        config_model=headers.get("config_type_name") or None,
    )


class FunctionArtifactBuilder:
    """Build immutable Linux artifacts before a revision becomes READY."""

    def __init__(self, storage_factory: FunctionStorageFactoryPort) -> None:
        self._storage_factory = storage_factory
        self._uv = settings.function_builder_executable
        self._python_platform = (
            settings.function_builder_python_platform or FUNCTION_PYTHON_PLATFORM
        )
        self._builder_digest = settings.function_builder_digest

    async def build(
        self,
        *,
        function_id: UUID,
        code: str,
        python_packages: tuple[str, ...],
    ) -> FunctionArtifact:
        header = parse_runtime_header(code)
        build_root = Path(await run_blocking(tempfile.mkdtemp, prefix="lemma-fn-"))
        try:
            dependency_lock = await self._build_dependencies(
                build_root, python_packages
            )
            manifest = FunctionArtifactManifest(
                runtime_abi=RUNTIME_ABI,
                builder_digest=self._builder_digest,
                dependency_lock=dependency_lock,
                input_model=header.input_model,
                output_model=header.output_model,
                entrypoint=header.entrypoint,
                config_model=header.config_model,
                dependency_path=("site-packages" if python_packages else None),
            )
            archive = await run_blocking(
                self._archive,
                build_root,
                code,
                manifest,
            )
            # Offloaded like the archive build either side of it. This hashes
            # the whole function bundle -- user code plus resolved
            # site-packages -- so it grows with the dependency tree, and it sat
            # on the loop between two calls that were already careful not to.
            digest = await run_blocking(lambda: hashlib.sha256(archive).hexdigest())
            revision_hash = f"sha256:{digest}"
            artifact_path = f"artifacts/{revision_hash.removeprefix('sha256:')}.zip"
            await self._storage_factory(function_id).write_file(artifact_path, archive)
            return FunctionArtifact(
                revision_hash=revision_hash,
            )
        finally:
            await run_blocking(shutil.rmtree, build_root, True)

    async def _build_dependencies(
        self, root: Path, packages: tuple[str, ...]
    ) -> tuple[str, ...]:
        if not packages:
            return ()
        requirements = root / "requirements.in"
        lock = root / "requirements.lock"
        await run_blocking(
            requirements.write_text,
            "\n".join(packages) + "\n",
            "utf-8",
        )
        await self._run_builder(
            "pip",
            "compile",
            str(requirements),
            "--output-file",
            str(lock),
            "--python-version",
            FUNCTION_PYTHON_VERSION,
            "--python-platform",
            self._python_platform,
            "--no-build",
            "--no-emit-index-url",
            "--no-header",
        )
        target = root / "site-packages"
        await self._run_builder(
            "pip",
            "install",
            "--target",
            str(target),
            "--requirements",
            str(lock),
            "--python-version",
            FUNCTION_PYTHON_VERSION,
            "--python-platform",
            self._python_platform,
            "--no-build",
            "--no-cache",
        )
        lines = tuple(
            line.strip()
            for line in (await run_blocking(lock.read_text, "utf-8")).splitlines()
            if line.strip() and not line.startswith("#")
        )
        return lines

    async def _run_builder(self, *arguments: str) -> None:
        try:
            process = await asyncio.create_subprocess_exec(
                self._uv,
                *arguments,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "UV_NO_PROGRESS": "1"},
            )
        except FileNotFoundError as exc:
            raise FunctionValidationError(
                "Function dependency builder is not installed"
            ) from exc
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            detail = (stderr or stdout).decode(errors="replace")[-2000:]
            raise FunctionValidationError(
                f"Function dependencies could not be resolved: {detail}"
            )

    @staticmethod
    def _archive(
        root: Path,
        code: str,
        manifest: FunctionArtifactManifest,
    ) -> bytes:
        source = root / "function.py"
        manifest_path = root / "manifest.json"
        source.write_text(code, encoding="utf-8")
        manifest_path.write_text(manifest.model_dump_json(), encoding="utf-8")
        descriptor, output_name = tempfile.mkstemp(
            prefix="lemma-fn-artifact-", suffix=".zip"
        )
        os.close(descriptor)
        output = Path(output_name)
        try:
            with zipfile.ZipFile(
                output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
            ) as archive:
                paths = [source, manifest_path]
                dependencies = root / "site-packages"
                if dependencies.exists():
                    paths.extend(
                        path
                        for path in dependencies.rglob("*")
                        if path.is_file() and "__pycache__" not in path.parts
                    )
                for path in sorted(paths, key=lambda item: str(item.relative_to(root))):
                    relative = str(path.relative_to(root))
                    info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = 0o644 << 16
                    archive.writestr(info, path.read_bytes())
            return output.read_bytes()
        finally:
            output.unlink(missing_ok=True)
