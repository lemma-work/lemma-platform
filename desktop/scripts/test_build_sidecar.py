"""Exercise native/cross sidecar build selection without compiling or signing."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


@unittest.skipIf(os.name == "nt", "exec shims use Unix executable scripts")
class SidecarBuildTests(unittest.TestCase):
    def run_build(
        self, host: str, target_env: str | None = None, *, powershell: bool = False
    ) -> list[str]:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scripts = root / "desktop/scripts"
            scripts.mkdir(parents=True)
            script = "build-sidecar.ps1" if powershell else "build-sidecar.sh"
            triple = "x86_64-pc-windows-msvc" if powershell else "aarch64-apple-darwin"
            shutil.copyfile(Path(__file__).with_name(script), scripts / script)
            shims = root / "shims"
            shims.mkdir()
            bodies = {
                "rustc": f"print('host: {host}')",
                "cargo": """
import json, os, pathlib, sys
args = sys.argv[1:]
pathlib.Path('cargo-args.json').write_text(json.dumps(args))
out = pathlib.Path('desktop/target')
if '--target' in args:
    out /= args[args.index('--target') + 1]
out /= 'release'
out.mkdir(parents=True, exist_ok=True)
for name in ('lemma-locald', 'lemma-agent-host', 'lemma-runtime'):
    suffix = '.exe' if os.environ['LEMMA_SIDECAR_TRIPLE'].endswith('windows-msvc') else ''
    executable = out / (name + suffix)
    executable.write_text('#!/bin/sh\\nexit 0\\n')
    executable.chmod(0o755)
""",
                "swift": """
import pathlib
out = pathlib.Path('desktop/local-runtime/macos-vz/.build/arm64-apple-macosx/release/lemma-vz')
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text('#!/bin/sh\\nexit 0\\n')
out.chmod(0o755)
""",
                "codesign": """
import pathlib, sys
if '-dv' in sys.argv:
    name = pathlib.Path(sys.argv[-1]).name.removeprefix('lemma-').removesuffix('-aarch64-apple-darwin')
    print('Identifier=work.lemma.' + name, file=sys.stderr)
if '--entitlements' in sys.argv:
    print('com.apple.security.virtualization')
""",
            }
            for name, body in bodies.items():
                shim = shims / name
                shim.write_text(f"#!{sys.executable}\n{body}\n")
                shim.chmod(0o755)
            env = dict(
                os.environ,
                PATH=f"{shims}{os.pathsep}{os.environ['PATH']}",
                APPLE_SIGNING_IDENTITY="-",
                LEMMA_SIDECAR_TRIPLE=triple,
            )
            env.pop("CARGO_BUILD_TARGET", None)
            if target_env:
                env["CARGO_BUILD_TARGET"] = target_env
            result = subprocess.run(
                (["pwsh", "-NoProfile", "-File"] if powershell else ["bash"])
                + [str(scripts / script)],
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            names = (
                ("locald", "agent-host", "runtime")
                if powershell
                else ("locald", "agent-host", "runtime", "vz")
            )
            suffix = ".exe" if powershell else ""
            for name in names:
                self.assertTrue(
                    (root / f"desktop/binaries/lemma-{name}-{triple}{suffix}").is_file()
                )
            return json.loads((root / "cargo-args.json").read_text())

    def test_native_build_reuses_tauri_release_tree(self):
        args = self.run_build("aarch64-apple-darwin")
        self.assertNotIn("--target", args)
        self.assertIn("--locked", args)

    def test_cross_build_retains_explicit_target(self):
        args = self.run_build("x86_64-apple-darwin")
        self.assertEqual(args[args.index("--target") + 1], "aarch64-apple-darwin")

    def test_cargo_target_environment_keeps_target_directory(self):
        args = self.run_build("aarch64-apple-darwin", "aarch64-apple-darwin")
        self.assertEqual(args[args.index("--target") + 1], "aarch64-apple-darwin")

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell is required")
    def test_windows_native_build_reuses_tauri_release_tree(self):
        args = self.run_build("x86_64-pc-windows-msvc", powershell=True)
        self.assertNotIn("--target", args)
        self.assertIn("--locked", args)

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell is required")
    def test_windows_cross_build_retains_target(self):
        args = self.run_build("aarch64-apple-darwin", powershell=True)
        self.assertEqual(args[args.index("--target") + 1], "x86_64-pc-windows-msvc")
