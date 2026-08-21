from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Sentinel for the offline starter that ships inside the package. This is the
# default so `lemma apps init` works with no network. `--template <url|path>`
# overrides it with a git URL (cloned) or a local directory (copied).
BUNDLED_TEMPLATE_SOURCE = "bundled"
DEFAULT_TEMPLATE_SOURCE = BUNDLED_TEMPLATE_SOURCE
SUPPORTED_STYLE_PRESETS = {"neobrutal", "editorial", "soft", "terminal"}


@dataclass(frozen=True)
class AppScaffoldOptions:
    target_dir: Path
    name: str
    pod_id: str
    api_url: str
    auth_url: str
    title: str
    navigation: str
    agent_name: str | None
    chat_mode: str
    members: bool
    search_config: dict[str, Any] | None
    theme_toggle: bool
    install: bool
    registry: bool
    style_preset: str
    template_source: str
    # Optional local checkout of the `lemma-sdk` npm package. When set, the
    # scaffold pins `package.json`'s `lemma-sdk` dependency to `file:<path>` so
    # offline installs resolve against the local SDK instead of the registry.
    sdk_path: str | None = None
    # When True, the dev server proxies API calls through a same-origin `/api`
    # path (no cross-origin CORS in dev). See `_env_local` and the bundled
    # `vite.config.ts` dev proxy.
    proxy: bool = False


def scaffold_app(options: AppScaffoldOptions) -> list[str]:
    target_dir = options.target_dir.resolve()
    if options.registry:
        raise ValueError(
            "registry scaffolding is no longer part of `lemma apps init`. "
            "Initialize the Vite app template and build local components instead."
        )

    _ensure_fresh_directory(target_dir)
    used_bundled = _copy_template(target_dir, options.template_source)
    if used_bundled:
        _customize_bundled_template(target_dir, options)
        template_label = "bundled offline starter"
    else:
        _customize_template(target_dir, options)
        template_label = options.template_source

    steps = [f"created {target_dir}", f"used template {template_label}"]
    if options.install:
        package_manager = _detect_package_manager(target_dir)
        _run(
            _install_command_for_package_manager(target_dir, package_manager),
            cwd=target_dir,
        )
        steps.append(f"installed dependencies with {package_manager}")
    return steps


def normalize_navigation(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    aliases = {
        "single": "single-page",
        "single-page": "single-page",
        "sidebar": "sidebar",
        "side-bar": "sidebar",
        "topbar": "topbar",
        "top-bar": "topbar",
    }
    if normalized not in aliases:
        raise ValueError("navigation must be one of: sidebar, topbar, single-page")
    return aliases[normalized]


def normalize_chat_mode(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    aliases = {
        "page": "page",
        "popup": "popup",
        "right-sidebar": "right-sidebar",
        "side-panel": "right-sidebar",
    }
    if normalized not in aliases:
        raise ValueError("chat mode must be one of: page, popup, right-sidebar")
    return aliases[normalized]


def normalize_style_preset(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    aliases = {
        "neo-brutal": "neobrutal",
        "neobrutal": "neobrutal",
        "neobrutalism": "neobrutal",
        "editorial": "editorial",
        "soft": "soft",
        "soft-saas": "soft",
        "saas": "soft",
        "terminal": "terminal",
    }
    if normalized not in aliases:
        choices = ", ".join(sorted(SUPPORTED_STYLE_PRESETS))
        raise ValueError(f"style preset must be one of: {choices}")
    return aliases[normalized]


def load_search_config(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    value = raw
    if raw.startswith("@"):
        value = Path(raw[1:]).read_text(encoding="utf-8")
    parsed = json.loads(value)
    if isinstance(parsed, list):
        parsed = {"tables": parsed}
    if not isinstance(parsed, dict):
        raise ValueError("search config must be a JSON object or array")
    tables = parsed.get("tables") or []
    if not isinstance(tables, list):
        raise ValueError("search config tables must be an array")
    normalized_tables = []
    for index, table in enumerate(tables):
        if not isinstance(table, dict):
            raise ValueError(f"search table at index {index} must be an object")
        table_name = str(
            table.get("tableName") or table.get("table_name") or ""
        ).strip()
        fields = table.get("searchFields") or table.get("search_fields") or []
        if not table_name:
            raise ValueError(f"search table at index {index} is missing tableName")
        if not isinstance(fields, list) or not fields:
            raise ValueError(f"search table {table_name} needs searchFields")
        normalized_tables.append(
            {
                "tableName": table_name,
                "label": str(table.get("label") or table_name),
                "searchFields": [str(field) for field in fields],
                "displayField": str(table.get("displayField") or ""),
                "subtitleField": str(table.get("subtitleField") or ""),
                "hrefTemplate": str(
                    table.get("hrefTemplate") or f"/records/{table_name}/:id"
                ),
            }
        )
    files = parsed.get("files")
    normalized_files = None
    if isinstance(files, dict) and files.get("enabled", True):
        normalized_files = {
            "enabled": True,
            "label": str(files.get("label") or "Files"),
            "hrefTemplate": str(files.get("hrefTemplate") or "/files?path=:path"),
        }
    if not normalized_tables and not normalized_files:
        raise ValueError("search config must include tables or enabled files")
    return {"tables": normalized_tables, "files": normalized_files}


def app_name_from_path(path: Path) -> str:
    name = path.resolve().name or "lemma-app"
    return _slugify(name)


def title_from_name(name: str) -> str:
    return " ".join(
        part.capitalize() for part in name.replace("_", "-").split("-") if part
    )


def _load_html_starter_template() -> str:
    """The no-build HTML app starter, shipped as a package data file."""
    from importlib.resources import files

    return (
        files("lemma_cli.cli_core.assets")
        .joinpath("app_html_starter.html")
        .read_text(encoding="utf-8")
    )


def scaffold_html_app(target_dir: Path, *, title: str) -> list[str]:
    """Write a single no-build authenticated ``index.html`` starter.

    Unlike :func:`scaffold_app`, this clones no template and installs nothing —
    it writes one polished, themed page wired to the host-served browser SDK and
    runtime config: it greets the signed-in user, auto-discovers a table, and
    renders live charts plus copy-pasteable SDK snippets to build from.
    """
    target_dir = target_dir.resolve()
    _ensure_fresh_directory(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_title = (
        (title or "Lemma App")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    index_path = target_dir / "index.html"
    index_path.write_text(
        _load_html_starter_template().replace("__LEMMA_APP_TITLE__", safe_title),
        encoding="utf-8",
    )
    return [f"wrote {index_path}"]


def _ensure_fresh_directory(path: Path) -> None:
    if path.exists() and any(path.iterdir()):
        raise ValueError(f"{path} is not empty. Choose an empty directory.")


def _copy_template(root: Path, template_source: str) -> bool:
    """Materialize the app template into ``root``.

    Returns ``True`` when the offline bundled starter was used (so the caller
    runs the bundled customization path), ``False`` for an external git/local
    template.
    """
    root.parent.mkdir(parents=True, exist_ok=True)
    if template_source == BUNDLED_TEMPLATE_SOURCE:
        _copy_bundled_template(root)
        return True
    local_path = _local_template_path(template_source)
    if local_path is not None:
        if not local_path.exists() or not local_path.is_dir():
            raise ValueError(f"Lemma app template path does not exist: {local_path}")
        shutil.copytree(
            local_path,
            root,
            dirs_exist_ok=True,
            ignore=_template_ignore_patterns(),
        )
    else:
        _run(
            ["git", "clone", "--depth", "1", template_source, str(root)],
            cwd=root.parent,
        )
    _remove_template_artifacts(root)
    return False


def _copy_bundled_template(root: Path) -> None:
    """Copy the package-shipped offline Vite + React + lemma-sdk starter.

    The template files live in package data so this performs no network access.
    The shipped ``gitignore`` is renamed to ``.gitignore`` on the way out, since
    build tools commonly drop leading-dot files from package data.
    """
    from importlib.resources import as_file, files

    template_root = files("lemma_cli.cli_core.assets").joinpath("app_vite_template")
    if not template_root.is_dir():
        raise ValueError(
            "Bundled app template is missing from the package "
            "(lemma_cli/cli_core/assets/app_vite_template)."
        )
    root.mkdir(parents=True, exist_ok=True)
    with as_file(template_root) as template_dir:
        shutil.copytree(template_dir, root, dirs_exist_ok=True)
    packaged_gitignore = root / "gitignore"
    if packaged_gitignore.exists():
        packaged_gitignore.replace(root / ".gitignore")


def _local_template_path(template_source: str) -> Path | None:
    if template_source.startswith("file://"):
        return Path(template_source.removeprefix("file://")).expanduser()
    candidate = Path(template_source).expanduser()
    if candidate.exists():
        return candidate
    return None


def _template_ignore_patterns():
    return shutil.ignore_patterns(
        ".env",
        ".git",
        ".github",
        ".pnpm-store",
        ".tanstack",
        "node_modules",
        "dist",
        "coverage",
        ".vitest-attachments",
    )


def _remove_template_artifacts(root: Path) -> None:
    for relative_path in (
        ".git",
        ".github",
        ".pnpm-store",
        ".tanstack",
        "node_modules",
        "dist",
        "coverage",
        ".vitest-attachments",
    ):
        path = root / relative_path
        if path.exists():
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
    env_file = root / ".env"
    if env_file.exists():
        env_file.unlink()


def _write_app_manifest(root: Path, options: AppScaffoldOptions) -> None:
    """Write the app manifest where the pod importer expects it.

    The importer reads ``apps/<name>/<name>.json`` (canonical) and also accepts
    a ``lemma.app.json`` alias. We write the canonical manifest at the bundle
    path the importer scans, and keep ``lemma.app.json`` at the project root for
    backwards compatibility with `lemma apps deploy` and older tooling.
    """
    manifest_json = _app_metadata_json(options)
    bundle_dir = root / "apps" / options.name
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / f"{options.name}.json").write_text(manifest_json, encoding="utf-8")
    (root / "lemma.app.json").write_text(manifest_json, encoding="utf-8")


def _customize_bundled_template(root: Path, options: AppScaffoldOptions) -> None:
    """Customize the offline bundled starter (known minimal file set)."""
    (root / ".env.local").write_text(_env_local(options), encoding="utf-8")
    (root / ".env.example").write_text(_env_example(), encoding="utf-8")
    if options.proxy:
        (root / ".env.development.local").write_text(
            _env_development_local(options), encoding="utf-8"
        )
    # The bundled starter ships the client at src/lemma-client.ts (no src/lib/).
    (root / "AGENTS.md").write_text(
        _agents_md(options, client_module="src/lemma-client.ts"), encoding="utf-8"
    )
    _write_app_manifest(root, options)

    _update_package_json(root / "package.json", options)
    _replace_in_file(root / "index.html", "__LEMMA_APP_TITLE__", options.title)
    _replace_in_file(root / "src/main.tsx", "__LEMMA_APP_TITLE__", options.title)


def _customize_template(root: Path, options: AppScaffoldOptions) -> None:
    (root / ".env.local").write_text(_env_local(options), encoding="utf-8")
    (root / ".env.example").write_text(_env_example(), encoding="utf-8")
    if options.proxy:
        (root / ".env.development.local").write_text(
            _env_development_local(options), encoding="utf-8"
        )
    _write_app_manifest(root, options)
    (root / "AGENTS.md").write_text(_agents_md(options), encoding="utf-8")

    _update_package_json(root / "package.json", options)
    _replace_in_file(root / "index.html", "Lemma Vite Starter", options.title)
    _replace_in_file(
        root / "src/lib/lemma-client.ts", "Lemma Vite Starter", options.title
    )
    _replace_in_file(
        root / "src/components/layout/app-title.tsx", "Lemma Starter", options.title
    )
    _replace_in_file(
        root / "src/components/layout/app-title.tsx",
        "Vite + Lemma SDK",
        "Lemma app app",
    )
    _replace_in_file(
        root / "src/components/layout/data/sidebar-data.ts",
        "Lemma Starter",
        options.title,
    )
    _replace_in_file(
        root / "src/context/theme-provider.tsx",
        "const DEFAULT_STYLE_PRESET: StylePreset = 'default'",
        f"const DEFAULT_STYLE_PRESET: StylePreset = '{options.style_preset}'",
    )
    _ensure_vite_base(root / "vite.config.ts")


def _update_package_json(path: Path, options: AppScaffoldOptions) -> None:
    package = json.loads(path.read_text(encoding="utf-8"))
    package["name"] = options.name
    package["version"] = "0.0.0"
    package["description"] = f"{options.title} Lemma app app."
    sdk_spec = _resolve_lemma_sdk_spec(options.sdk_path)
    if sdk_spec is not None:
        package.setdefault("dependencies", {})["lemma-sdk"] = sdk_spec
    path.write_text(json.dumps(package, indent=2) + "\n", encoding="utf-8")


def resolve_local_sdk_path() -> Path | None:
    """Best-effort discovery of a sibling ``lemma-sdk`` (``lemma-typescript``)
    checkout, so dogfood scaffolds resolve the SDK offline without a flag.

    Walks up from this package looking for a sibling ``lemma-typescript``
    directory whose ``package.json`` declares ``"name": "lemma-sdk"``.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "lemma-typescript"
        package_json = candidate / "package.json"
        if package_json.exists():
            try:
                name = json.loads(package_json.read_text(encoding="utf-8")).get("name")
            except OSError, ValueError:
                name = None
            if name == "lemma-sdk":
                return candidate
    return None


def _latest_published_lemma_sdk_version() -> str | None:
    """Concrete latest published ``lemma-sdk`` version from the npm registry, or
    None when npm/the registry is unavailable (offline). Best-effort; never raises.
    Factored out so tests can stub the registry lookup."""
    try:
        result = subprocess.run(
            ["npm", "view", "lemma-sdk", "version"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except OSError, subprocess.SubprocessError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _resolve_lemma_sdk_spec(sdk_path: str | None) -> str | None:
    """Return the ``lemma-sdk`` dependency spec to write into the scaffold, or
    None to leave the template's default (``"latest"``) untouched.

    Precedence:
    1. ``--sdk-path`` → ``file:<abs>`` (explicit local checkout).
    2. A sibling ``lemma-typescript`` checkout → ``file:<abs>`` (offline dogfood).
    3. The concrete latest published version → ``^<version>``, so the generated
       project rides the newest SDK available *at init time* and then stays
       stable (rather than a forever-floating ``"latest"``).
    4. None when the registry is unreachable — the template's ``"latest"`` stays,
       so ``npm install`` still pulls the newest available.
    """
    if sdk_path:
        resolved = Path(sdk_path).expanduser().resolve()
        if not (resolved / "package.json").exists():
            raise ValueError(f"--sdk-path is not an npm package: {resolved}")
        return f"file:{resolved}"
    detected = resolve_local_sdk_path()
    if detected is not None:
        return f"file:{detected}"
    latest = _latest_published_lemma_sdk_version()
    if latest:
        return f"^{latest}"
    return None


def _replace_in_file(path: Path, before: str, after: str) -> None:
    # External templates may not ship every file the legacy customizer touches;
    # a missing file is not fatal, it just means there is nothing to rewrite.
    if not path.exists():
        return
    content = path.read_text(encoding="utf-8")
    path.write_text(content.replace(before, after), encoding="utf-8")


def _ensure_vite_base(path: Path) -> None:
    content = path.read_text(encoding="utf-8")
    if "loadEnv" in content and "env.VITE_LEMMA_APP_BASE_PATH" in content:
        return

    content = content.replace(
        "import { defineConfig } from 'vite'",
        "import { defineConfig, loadEnv } from 'vite'",
    )
    content = content.replace(
        'import { defineConfig } from "vite"',
        'import { defineConfig, loadEnv } from "vite"',
    )
    content = content.replace(
        "export default defineConfig({\n  plugins:",
        "export default defineConfig(({ mode }) => {\n"
        "  const env = loadEnv(mode, process.cwd(), '')\n\n"
        "  return {\n"
        "    base:\n"
        "      mode === 'production' ? env.VITE_LEMMA_APP_BASE_PATH || '/' : '/',\n"
        "    plugins:",
    )
    if content.rstrip().endswith("})"):
        content = content.rstrip()[:-2] + "  }\n})\n"
    path.write_text(content, encoding="utf-8")


def _app_metadata_json(options: AppScaffoldOptions) -> str:
    metadata = {
        "name": options.name,
        "title": options.title,
        "template": "lemma-vite",
        "podId": options.pod_id,
        "stylePreset": options.style_preset,
        "preferredStylePresets": ["neobrutal", "editorial", "soft", "terminal"],
        "runtime": {
            "apiUrl": options.api_url,
            "authUrl": options.auth_url,
            # Apps are served by host at the root of their subdomain.
            "appBasePath": "/",
        },
        "legacyInitHints": {
            "navigation": options.navigation,
            "agentName": options.agent_name,
            "chatMode": options.chat_mode,
            "members": options.members,
            "search": options.search_config,
            "themeToggle": options.theme_toggle,
        },
        "aiEditingContract": {
            "primaryInstruction": "Build the app as a native React app. Replace sample feature routes and data with the real operator workflow.",
            "avoid": [
                "Do not preserve sample dashboard/tasks/users/chats/apps pages unless they match the workflow.",
                "Do not add registry blocks or generated registry structure.",
                "Do not make a landing page; the first screen should start with work.",
            ],
        },
    }
    return json.dumps(metadata, indent=2) + "\n"


def _agents_md(
    options: AppScaffoldOptions, client_module: str = "src/lib/lemma-client.ts"
) -> str:
    return f"""# {options.title}

This is a Lemma app app generated from the Vite template.

## Runtime

- App name: `{options.name}`
- Pod id: `{options.pod_id}`
- API URL: `{options.api_url}`
- Auth URL: `{options.auth_url}`
- Served by host at the root of the app subdomain (base path `/`)

## Product Direction

- Build native React routes and components for the operator workflow.
- Replace sample routes, sample data, sidebar labels, fake users, placeholder metrics, and starter copy.
- Keep Lemma auth centralized through `AuthGuard`.
- Use `lemma-sdk/react` hooks and the shared `lemmaClient` from `{client_module}`.
- Preferred style presets: neobrutal, editorial, soft, terminal.
- Current default style preset: `{options.style_preset}`.
- Do not use Lemma registry scaffolding in this project.

## Calling the API

- Fetch once with hooks (`useRecords`, `useDatastoreQuery`, generated `use<Resource>List`). Never call `lemmaClient.records.list(...)` during render or in a `useEffect` with unstable deps — that loops.
- Realtime = subscribe, never poll. Use `useLiveRecords` for a live list (fetches once, then merges row deltas in place over the table WebSocket) or `useWatchChanges` for custom state. Never `setInterval(refetch)` — polling flickers and hammers the API.
- Don't flicker or reload: merge changes in place keyed by `record_id`, give list rows a stable `key` (the row id), and create the client once + gate auth once with `AuthGuard`.
- Writes through the generated CRUD hooks under `QueryClientProvider` auto-refresh the matching list — don't hand-wire a refetch.

## Verification

- Run the local dev server with the package manager used by the project.
- Run `npm run build`, `pnpm run build`, or `yarn run build` before deploy.
- Deploy with `lemma apps deploy {options.name} --source-dir . --yes`.
"""


def _run(command: list[str], *, cwd: Path) -> None:
    _require_command(command[0])
    subprocess.run(command, cwd=cwd, check=True)


def _detect_package_manager(source_dir: Path) -> str:
    if (source_dir / "pnpm-lock.yaml").exists() and shutil.which("pnpm"):
        return "pnpm"
    if (source_dir / "yarn.lock").exists() and shutil.which("yarn"):
        return "yarn"
    return "npm"


def _install_command_for_package_manager(
    source_dir: Path, package_manager: str
) -> list[str]:
    if package_manager == "npm":
        if (source_dir / "package-lock.json").exists():
            return ["npm", "ci"]
        return ["npm", "install"]
    if package_manager == "pnpm":
        if (source_dir / "pnpm-lock.yaml").exists():
            return ["pnpm", "install", "--frozen-lockfile"]
        return ["pnpm", "install"]
    if package_manager == "yarn":
        if (source_dir / "yarn.lock").exists():
            return ["yarn", "install", "--frozen-lockfile"]
        return ["yarn", "install"]
    raise ValueError(f"Unsupported package manager: {package_manager}")


def _require_command(command: str) -> None:
    if shutil.which(command) is None:
        raise ValueError(f"{command} is required.")


def _slugify(value: str) -> str:
    chars = []
    previous_dash = False
    for char in value.lower():
        if char.isalnum():
            chars.append(char)
            previous_dash = False
        elif not previous_dash:
            chars.append("-")
            previous_dash = True
    return "".join(chars).strip("-") or "lemma-app"


def _env_value(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _env_local(options: AppScaffoldOptions) -> str:
    values = {
        "VITE_LEMMA_API_URL": options.api_url,
        "VITE_LEMMA_AUTH_URL": options.auth_url,
        "VITE_LEMMA_POD_ID": options.pod_id,
        # Apps are served by host at the root of their subdomain.
        "VITE_LEMMA_APP_BASE_PATH": "/",
    }
    if options.agent_name:
        values["VITE_LEMMA_AGENT_NAME"] = options.agent_name
    return (
        "\n".join(f"{key}={_env_value(value)}" for key, value in values.items()) + "\n"
    )


def _env_development_local(options: AppScaffoldOptions) -> str:
    """Dev-only env overrides for `--proxy`.

    Vite loads ``.env.development.local`` ONLY in dev (`serve`), never in
    ``vite build`` — so deploys keep the real ``VITE_LEMMA_API_URL`` from
    ``.env.local``. In dev the SDK calls a same-origin ``/api`` path that the Vite
    dev proxy forwards to the backend (``LEMMA_DEV_PROXY_TARGET``), avoiding CORS.
    """
    values = {
        "VITE_LEMMA_API_URL": "/api",
        # Non-VITE var: read only by vite.config (the dev proxy target).
        "LEMMA_DEV_PROXY_TARGET": options.api_url,
    }
    return (
        "\n".join(f"{key}={_env_value(value)}" for key, value in values.items()) + "\n"
    )


def _env_example() -> str:
    values = {
        "VITE_LEMMA_API_URL": "https://api.lemma.work",
        "VITE_LEMMA_AUTH_URL": "https://lemma.work/auth",
        "VITE_LEMMA_POD_ID": "",
        "VITE_LEMMA_APP_NAME": "My Lemma App",
        "VITE_LEMMA_APP_BASE_PATH": "/",
    }
    return (
        "\n".join(f"{key}={_env_value(value)}" for key, value in values.items()) + "\n"
    )
