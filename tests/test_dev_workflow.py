from __future__ import annotations

import base64
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
MAKEFILE = ROOT / "Makefile"


class DevWorkflowTests(unittest.TestCase):
    def run_make(
        self,
        cwd: Path,
        *targets: str,
        variables: dict[str, str] | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        command = ["make", "--no-print-directory", "-f", str(MAKEFILE), *targets]
        command.extend(f"{key}={value}" for key, value in (variables or {}).items())
        return subprocess.run(
            command,
            cwd=cwd,
            env={**os.environ, "AGENTBOX_ENDPOINT_STATE_KEYS": ""},
            text=True,
            capture_output=True,
            check=check,
        )

    @staticmethod
    def env_values(path: Path) -> dict[str, str]:
        values: dict[str, str] = {}
        for line in path.read_text().splitlines():
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key] = value
        return values

    def test_init_generates_complete_local_config_and_stable_agentbox_key(self):
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            backend = tmp / "backend"
            agentbox = tmp / "agentbox"
            backend.mkdir()
            agentbox.mkdir()
            variables = {
                "BACKEND_DIR": str(backend),
                "AGENTBOX_DIR": str(agentbox),
            }

            self.run_make(
                tmp,
                "_init-backend-env",
                "_init-agentbox-env",
                variables=variables,
            )

            backend_env = self.env_values(backend / ".env")
            self.assertEqual(backend_env["ENVIRONMENT"], "local")
            self.assertEqual(backend_env["LOG_LEVEL"], "DEBUG")
            self.assertTrue(backend_env["DATASTORE_DATABASE_URL"].endswith("/lemma_datastore"))
            self.assertEqual(backend_env["DOCUMENT_PROCESSOR"], "markitdown")
            self.assertEqual(backend_env["KREUZBERG_URL"], "")
            self.assertEqual(backend_env["EMAIL_TRANSPORT"], "filesystem")
            self.assertEqual(backend_env["AUTH_EMAIL_VERIFICATION_REQUIRED"], "false")
            self.assertEqual(backend_env["API_URL"], "http://localhost:8710")
            self.assertEqual(backend_env["FRONTEND_URL"], "http://localhost:3710")

            key_before = self.env_values(agentbox / ".env")[
                "AGENTBOX_ENDPOINT_STATE_KEYS"
            ]
            decoded = base64.urlsafe_b64decode(key_before)
            self.assertEqual(len(decoded), 32)

            self.run_make(tmp, "_init-agentbox-env", variables=variables)
            key_after = self.env_values(agentbox / ".env")[
                "AGENTBOX_ENDPOINT_STATE_KEYS"
            ]
            self.assertEqual(key_after, key_before)

    def test_ensure_backend_env_appends_only_missing_values(self):
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            backend = tmp / "backend"
            backend.mkdir()
            env_file = backend / ".env"
            env_file.write_text("API_URL=https://custom.example.test\n")

            self.run_make(
                tmp,
                "_ensure-backend-env-keys",
                variables={"BACKEND_DIR": str(backend)},
            )

            text = env_file.read_text()
            lines = text.splitlines()
            self.assertEqual(sum(line.startswith("API_URL=") for line in lines), 1)
            self.assertIn("API_URL=https://custom.example.test", text)
            self.assertEqual(
                sum(line.startswith("DATASTORE_DATABASE_URL=") for line in lines), 1
            )
            self.assertEqual(
                sum(line.startswith("AGENTBOX_API_KEY=") for line in lines), 1
            )

    def test_wait_agentbox_reports_exited_unified_backend(self):
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            backend = tmp / "backend"
            backend.mkdir()
            (backend / ".dev-backend.pid").write_text("99999999\n")

            result = self.run_make(
                tmp,
                "_wait-agentbox",
                variables={
                    "BACKEND_DIR": str(backend),
                    "DEV_BACKEND_PORT": "1",
                    "AGENTBOX_READY_TIMEOUT": "1",
                },
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            output = result.stdout + result.stderr
            self.assertIn(
                "Unified backend exited before embedded AgentBox became ready", output
            )

    def test_unified_backend_embeds_agentbox_with_postgres_state(self):
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            result = self.run_make(
                tmp,
                "-n",
                "_run-backend",
                variables={
                    "AGENTBOX_ENDPOINT_STATE_KEYS": "test-key",
                },
            )

            self.assertIn("AGENTBOX_STATE_DATABASE_URL=postgresql://", result.stdout)
            self.assertIn("AGENTBOX_ENDPOINT_STATE_KEYS=test-key", result.stdout)
            self.assertIn("AGENTBOX_API_URL=http://127.0.0.1:8710/internal/agentbox", result.stdout)
            self.assertIn("DOCUMENT_PROCESSOR=markitdown", result.stdout)
            self.assertIn("uv run --extra local uvicorn local_app:app", result.stdout)

    def test_dev_starts_only_backend_and_frontend_app_processes(self):
        dev_recipe = MAKEFILE.read_text().split("\ndev:\n", 1)[1].split(
            "\ndev-public:\n", 1
        )[0]

        self.assertIn("_run-backend", dev_recipe)
        self.assertIn("_run-frontend", dev_recipe)
        self.assertNotIn("_run-agentbox", dev_recipe)

    def test_public_mode_tunnels_only_api_and_keeps_frontend_local(self):
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            tunnel = self.run_make(
                tmp,
                "-n",
                "_start-public-api-tunnel",
                variables={"DEV_LOG_DIR": str(tmp / "logs")},
            )
            self.assertEqual(tunnel.stdout.count("cloudflared tunnel"), 1)
            self.assertIn("--url http://127.0.0.1:8710", tunnel.stdout)
            self.assertNotIn("--url http://127.0.0.1:3710", tunnel.stdout)

            backend = self.run_make(
                tmp,
                "-n",
                "_run-backend",
                variables={"BACKEND_API_URL": "https://public-api.example.test"},
            )
            self.assertIn("API_URL=https://public-api.example.test", backend.stdout)
            self.assertIn("FRONTEND_URL=http://localhost:3710", backend.stdout)
            self.assertIn("AUTH_FRONTEND_URL=http://localhost:3710", backend.stdout)

            frontend = self.run_make(
                tmp,
                "-n",
                "_run-frontend",
                variables={"FRONTEND_API_URL": "https://public-api.example.test"},
            )
            self.assertIn(
                "NEXT_PUBLIC_API_URL=https://public-api.example.test",
                frontend.stdout,
            )
            self.assertIn("NEXT_PUBLIC_SITE_URL=http://localhost:3710", frontend.stdout)
            self.assertIn("NEXT_PUBLIC_AUTH_URL=http://localhost:3710", frontend.stdout)


if __name__ == "__main__":
    unittest.main()
