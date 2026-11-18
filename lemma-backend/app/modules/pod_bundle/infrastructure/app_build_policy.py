"""The ``pnpm-workspace.yaml`` an app that brings none is built with.

The sandbox's pnpm fails an install on a dependency install script nobody
approved. An app that ships its own workspace file is built with it as it is
(``app_builder.build_script``); for one that does not, this is the file.
"""

from __future__ import annotations

import io
import json
import posixpath
import zipfile

#: What an app that brings no ``pnpm-workspace.yaml`` is built with. The
#: sandbox's pnpm fails an install on any dependency whose install script it
#: was not told about, and Vite brings one (esbuild), so a Vite app without a
#: file of its own failed before its build ran. esbuild's script is allowed,
#: under pnpm 10's spelling and pnpm 11's (``packageManager`` can pick either).
#: Any other script is still not run: ``strictDepBuilds: false`` only stops one
#: from failing the install, because the Lemma SDK brings one too
#: (browser-tabs-lock, through its sign-in client), and allowing esbuild alone
#: still failed every app that uses the SDK.
DEFAULT_PNPM_WORKSPACE = """\
onlyBuiltDependencies:
  - esbuild
allowBuilds:
  esbuild: true
strictDepBuilds: false
"""


#: The keys of ``package.json -> pnpm`` that say which install scripts run.
#: pnpm 11, the sandbox's, no longer reads that field, and folds the lists
#: into one ``allowBuilds`` map; the same names anywhere else say nothing.
_OWN_BUILD_POLICY = frozenset(
    {
        "onlyBuiltDependencies",
        "allowBuilds",
        "neverBuiltDependencies",
        "ignoredBuiltDependencies",
        "onlyBuiltDependenciesFile",
    }
)


def _package_pnpm(source_zip: bytes) -> tuple[dict[str, object], list[str]]:
    """``package.json -> pnpm``, and the names in the file its
    ``onlyBuiltDependenciesFile`` names; nothing for what is missing or
    unreadable."""
    try:
        with zipfile.ZipFile(io.BytesIO(source_zip)) as archive:
            manifest = json.loads(archive.read("package.json"))
            pnpm = manifest.get("pnpm") if isinstance(manifest, dict) else None
            if not isinstance(pnpm, dict):
                return {}, []
            return pnpm, _listed_in(archive, pnpm.get("onlyBuiltDependenciesFile"))
    except KeyError, ValueError, zipfile.BadZipFile:
        return {}, []


def _listed_in(archive: zipfile.ZipFile, path: object) -> list[str]:
    """The names a JSON list file in the app holds, its path read from beside
    ``package.json`` (the archive's root); one outside the app is not read."""
    if not isinstance(path, str) or not path.strip():
        return []
    member = posixpath.normpath(path.strip().replace("\\", "/"))
    if member.startswith(("/", "../")) or member == "..":
        return []
    try:
        return _names(json.loads(archive.read(member)))
    except KeyError, ValueError:
        return []


def _names(value: object) -> list[str]:
    return (
        [str(n) for n in value if isinstance(n, str)] if isinstance(value, list) else []
    )


def workspace_for(source_zip: bytes) -> str:
    """The ``pnpm-workspace.yaml`` an app that brings none is built with.

    The default, unless the app's ``package.json`` gives a build policy under
    ``pnpm`` (lists, the ``allowBuilds`` map, or ``onlyBuiltDependenciesFile``,
    read from the app): pnpm 10 read that and pnpm 11 ignores it, so it is carried into
    the file, as one ``allowBuilds`` map (and pnpm 10's list), over the
    default's esbuild -- which the app may still refuse by name. An unlisted
    script still does not fail the install (``strictDepBuilds: false``)."""
    policy, from_file = _package_pnpm(source_zip)
    if _OWN_BUILD_POLICY.isdisjoint(policy):
        return DEFAULT_PNPM_WORKSPACE
    allow: dict[str, bool] = {"esbuild": True}
    allow.update(dict.fromkeys(_names(policy.get("onlyBuiltDependencies")), True))
    allow.update(dict.fromkeys(from_file, True))
    for key in ("neverBuiltDependencies", "ignoredBuiltDependencies"):
        allow.update(dict.fromkeys(_names(policy.get(key)), False))
    own = policy.get("allowBuilds")
    if isinstance(own, dict):
        allow.update({str(k): v for k, v in own.items() if isinstance(v, bool)})
    # Names quoted as JSON strings, which YAML reads alike: `@scope/pkg` is not
    # a plain YAML scalar.
    built = [name for name, runs in sorted(allow.items()) if runs]
    lines = ["onlyBuiltDependencies:" if built else "onlyBuiltDependencies: []"]
    lines += [f"  - {json.dumps(name)}" for name in built]
    lines.append("allowBuilds:")
    lines += [
        f"  {json.dumps(name)}: {'true' if runs else 'false'}"
        for name, runs in sorted(allow.items())
    ]
    lines.append("strictDepBuilds: false")
    return "\n".join(lines) + "\n"


__all__ = ["DEFAULT_PNPM_WORKSPACE", "workspace_for"]
