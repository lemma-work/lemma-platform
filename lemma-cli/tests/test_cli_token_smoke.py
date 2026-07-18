from __future__ import annotations

import httpx
from typer.testing import CliRunner

from lemma_cli.cli_core.app import app


runner = CliRunner()


def _write_config(path, base_url: str) -> None:
    path.write_text(
        '{\n'
        '  "active_server": "lemma-cloud",\n'
        '  "servers": {\n'
        '    "lemma-cloud": {\n'
        f'      "base_url": "{base_url}",\n'
        '      "defaults": {},\n'
        '      "auth": {\n'
        '        "access_token": "STORED_ACCESS",\n'
        '        "refresh_token": "STORED_REFRESH"\n'
        '      }\n'
        '    }\n'
        '  }\n'
        '}\n',
        encoding="utf-8",
    )


def _capture_authorization(monkeypatch) -> list[str | None]:
    captured: list[str | None] = []

    def fake_request(self, method, url, **kwargs):  # type: ignore[no-untyped-def]
        captured.append(self.headers.get("Authorization"))
        return httpx.Response(
            200,
            json={"items": []},
            request=httpx.Request(method, url),
        )

    monkeypatch.setattr(httpx.Client, "request", fake_request)
    return captured


def _clear_token_environment(monkeypatch) -> None:
    for name in ("LEMMA_TOKEN", "LEMMA_SERVER", "LEMMA_BASE_URL"):
        monkeypatch.delenv(name, raising=False)


def test_runtime_harnesses_uses_top_level_token_override(monkeypatch, tmp_path):
    _clear_token_environment(monkeypatch)
    config_file = tmp_path / "config.json"
    _write_config(config_file, "https://api.example.test")
    captured = _capture_authorization(monkeypatch)

    result = runner.invoke(
        app,
        [
            "--config-file",
            str(config_file),
            "--token",
            "FAKE_JWT",
            "runtime",
            "harnesses",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert captured == ["Bearer FAKE_JWT"]


def test_runtime_harnesses_uses_lemma_token_environment_override(
    monkeypatch, tmp_path
):
    _clear_token_environment(monkeypatch)
    monkeypatch.setenv("LEMMA_TOKEN", "FAKE_JWT")
    monkeypatch.setenv("LEMMA_BASE_URL", "https://api.example.test")
    config_file = tmp_path / "config.json"
    _write_config(config_file, "https://stored.example.test")
    captured = _capture_authorization(monkeypatch)

    result = runner.invoke(
        app,
        ["--config-file", str(config_file), "runtime", "harnesses", "--json"],
    )

    assert result.exit_code == 0, result.stdout
    assert captured == ["Bearer FAKE_JWT"]


def test_runtime_harnesses_falls_back_to_stored_access_token(monkeypatch, tmp_path):
    _clear_token_environment(monkeypatch)
    config_file = tmp_path / "config.json"
    _write_config(config_file, "https://api.example.test")
    captured = _capture_authorization(monkeypatch)

    result = runner.invoke(
        app,
        ["--config-file", str(config_file), "runtime", "harnesses", "--json"],
    )

    assert result.exit_code == 0, result.stdout
    assert captured == ["Bearer STORED_ACCESS"]
