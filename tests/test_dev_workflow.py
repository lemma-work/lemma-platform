from __future__ import annotations

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
            env=os.environ.copy(),
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

    def test_init_generates_complete_local_config_and_agentbox_override_file(self):
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
            self.assertEqual(backend_env["EMAIL_TRANSPORT"], "filesystem")
            self.assertEqual(backend_env["AUTH_EMAIL_VERIFICATION_REQUIRED"], "false")
            self.assertEqual(backend_env["API_URL"], "http://localhost:8710")
            self.assertEqual(backend_env["FRONTEND_URL"], "http://localhost:3710")

            agentbox_text = (agentbox / ".env").read_text()
            self.assertIn("AgentBox local overrides", agentbox_text)

            self.run_make(tmp, "_init-agentbox-env", variables=variables)
            self.assertEqual((agentbox / ".env").read_text(), agentbox_text)

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
            self.assertEqual(
                sum(line.startswith("APP_BASE_DOMAIN=") for line in lines), 1
            )

    def test_init_backend_env_includes_local_app_domain(self):
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            backend = tmp / "backend"
            backend.mkdir()

            self.run_make(
                tmp,
                "_init-backend-env",
                variables={"BACKEND_DIR": str(backend)},
            )

            backend_env = self.env_values(backend / ".env")
            self.assertEqual(
                backend_env["APP_BASE_DOMAIN"], "apps.lemma.localhost:8710"
            )

    def test_backend_and_frontend_receive_local_app_domains(self):
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)

            backend = self.run_make(tmp, "-n", "_run-backend")
            self.assertIn(
                "APP_BASE_DOMAIN=apps.lemma.localhost:8710", backend.stdout
            )

            frontend = self.run_make(tmp, "-n", "_run-frontend")
            self.assertIn(
                "NEXT_PUBLIC_APPS_DOMAIN_SUFFIX=apps.lemma.localhost",
                frontend.stdout,
            )

    def test_fresh_dev_database_imports_native_connector_catalog(self):
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)

            result = self.run_make(tmp, "-n", "_ensure-native-connectors")

            self.assertIn(
                "scripts/import_connector_catalog.py --provider native",
                result.stdout,
            )
            self.assertIn(
                "SELECT 1 FROM connectors WHERE id = 'telegram'",
                result.stdout,
            )

    def test_wait_agentbox_reports_exited_unified_backend_pid(self):
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
                    "DEV_AGENTBOX_URL": "http://127.0.0.1:1/internal/agentbox",
                    "AGENTBOX_READY_TIMEOUT": "1",
                },
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            output = result.stdout + result.stderr
            self.assertIn(
                "Unified backend exited before embedded AgentBox became ready",
                output,
            )
            self.assertIn("PID 99999999", output)

    def test_embedded_agentbox_launch_uses_psycopg_and_canonical_images(self):
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            result = self.run_make(
                tmp,
                "-n",
                "_run-backend",
            )

            self.assertIn(
                "AGENTBOX_STATE_DATABASE_URL=postgresql+psycopg://",
                result.stdout,
            )
            self.assertIn("AGENTBOX_WORKSPACE_IMAGE=agentbox-workspace:dev", result.stdout)
            self.assertIn("AGENTBOX_FUNCTION_IMAGE=agentbox-function:dev", result.stdout)
            self.assertIn("uv run --extra local uvicorn local_app:app", result.stdout)

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
