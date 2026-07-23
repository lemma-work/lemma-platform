from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
OPENAPI_DIR = ROOT / "openapi"
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head"}


def _add_repo_paths() -> None:
    for path in [REPO_ROOT / "agentbox", ROOT]:
        value = str(path)
        if value not in sys.path:
            sys.path.insert(0, value)


def _write_json(path: Path, payload: dict, *, check: bool) -> bool:
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if check:
        return path.exists() and path.read_text() == text
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return True


def _normalize_operation_ids(openapi: dict) -> dict:
    paths = openapi.get("paths")
    if not isinstance(paths, dict):
        return openapi
    for path, methods in paths.items():
        if not isinstance(path, str) or not isinstance(methods, dict):
            continue
        path_slug = re.sub(r"[^a-zA-Z0-9]+", "_", path).strip("_").lower()
        for method, operation in methods.items():
            if method.lower() not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            operation["operationId"] = f"{method.lower()}_{path_slug or 'root'}"
    return openapi


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    _add_repo_paths()
    os.environ.setdefault("AGENTBOX_API_KEY", "openapi-generation-key")
    os.environ.setdefault("AGENTBOX_API_URL", "http://127.0.0.1:8721")

    from agentbox.api.app import app

    ok = True
    ok &= _write_json(
        OPENAPI_DIR / "agentbox-manager.openapi.json",
        _normalize_operation_ids(app.openapi()),
        check=args.check,
    )

    if args.check and not ok:
        print("agentbox-client generated OpenAPI snapshots are stale", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
