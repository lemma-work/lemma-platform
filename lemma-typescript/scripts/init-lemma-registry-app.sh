#!/usr/bin/env bash

# -----------------------------------------------------------------------------
# init-lemma-registry-app.sh
#
# Maintainer README
#
# What this script is:
# - A standalone Lemma desk scaffolder for AI agents and developers.
# - It is intentionally NOT part of the published `lemma-sdk` runtime surface.
# - It creates a Vite + React + TypeScript app, wires `lemma-sdk`, installs the
#   Lemma registry components, and generates a light desk shell.
#
# What this script is not:
# - It is not an SDK CLI command.
# - It is not expected to import local repo files at runtime.
# - It should remain portable as a single bash file plus public registry access.
#
# Standalone contract:
# - Registry items are fetched remotely from jsDelivr via `LEMMA_REGISTRY_URL`.
# - The script should work even if copied outside this repository.
# - The only expected external requirements are:
#   - `bash`
#   - `node`
#   - `npm`
#   - `npx`
#   - network access to npm + jsDelivr
#
# High-level flow:
# 1. Parse flags or prompt interactively.
# 2. Create a fresh Vite React app.
# 3. Install app dependencies and `lemma-sdk`.
# 4. Install the full Lemma registry set locally into `src/components/lemma`.
# 5. Generate desk shell files:
#    - auth-wrapped app shell
#    - optional search wiring
#    - optional assistant wiring
#    - optional members page
#    - optional theme toggle
#    - profile menu with SDK logout
# 6. Write a warm default Lemma visual theme into `src/index.css`.
#
# Key behavior decisions:
# - Full registry install is intentional so the generated app has all registry
#   components available locally for later AI edits without another download.
# - The script uses `shadcn add --overwrite` to stay non-blocking even when
#   multiple registry items target overlapping helper files.
# - `page` is the only assistant mode that creates a dedicated `/assistant`
#   route. `popup` and `right-sidebar` remain embedded surfaces.
# - Theme toggle is enabled by default and can be disabled with
#   `--no-theme-toggle`.
#
# Porting notes:
# - If you move this into another repo or CLI package, keep the following
#   pieces aligned:
#   - `LEMMA_REGISTRY_URL`
#   - registry item list in `LEMMA_REGISTRY_ITEMS`
#   - generated app templates in this file
# - If the remote registry content changes shape, update only the installer and
#   template assumptions here; the goal is to keep the script single-file.
#
# Safe modification checklist:
# - After edits, run: `bash -n scripts/init-lemma-registry-app.sh`
# - Prefer testing at least one scaffold path end-to-end.
# - If changing generated TypeScript/React templates, also run `npm run build`
#   inside a freshly scaffolded app.
# -----------------------------------------------------------------------------

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LEMMA_REGISTRY_URL="https://lemma-work.github.io/lemma-platform/r/{name}.json"
SHADCN_SCHEMA_URL="https://ui.shadcn.com/schema.json"

TARGET_DIR="."
DESK_NAME=""
POD_ID=""
APP_TITLE=""
NAVIGATION=""
SEARCH_ENABLED=""
SEARCH_CONFIG_RAW=""
ASSISTANT_ENABLED=""
ASSISTANT_NAME=""
ASSISTANT_MODE=""
MEMBERS_ENABLED=""
THEME_TOGGLE_ENABLED="true"
AUTO_YES="0"
SEARCH_CONFIG_JSON="null"
ASSISTANT_CONFIG_JSON="null"

usage() {
  cat <<'EOF'
Usage:
  bash scripts/init-lemma-registry-app.sh [target-dir] --pod-id <pod-id> [options]

Options:
  --desk-name <name>
  --title <title>
  --nav <single-page|sidebar|topbar>
  --search
  --no-search
  --search-config <json-or-@file>
  --assistant <name> <page|popup|right-sidebar>
  --no-assistant
  --members
  --no-members
  --theme-toggle
  --no-theme-toggle
  --assistant-name <name>
  --assistant-mode <page|popup|right-sidebar>
  -y, --yes

Examples:
  bash scripts/init-lemma-registry-app.sh --desk-name my-desk --pod-id pod_123

  bash scripts/init-lemma-registry-app.sh --desk-name my-desk \
    --pod-id pod_123 \
    --nav sidebar \
    --search \
    --search-config '{"tables":[{"tableName":"issues","label":"Issues","searchFields":["identifier","title","status"],"displayField":"title","subtitleField":"status"}]}' \
    --members \
    --theme-toggle \
    --assistant research-assistant right-sidebar
EOF
}

fail() {
  echo "init-lemma-registry-app: $1" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "$1 is required."
}

trim() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}

normalize_nav() {
  case "$1" in
    single-page|single|single_page) printf 'single-page' ;;
    sidebar|side-bar) printf 'sidebar' ;;
    topbar|top-bar|top_bar) printf 'topbar' ;;
    *) printf '' ;;
  esac
}

normalize_assistant_mode() {
  case "$1" in
    page) printf 'page' ;;
    popup) printf 'popup' ;;
    right-sidebar|right_sidebar|side-panel) printf 'right-sidebar' ;;
    *) printf '' ;;
  esac
}

humanize_name() {
  node -e 'const value=(process.argv[1]||"lemma-desk").replace(/[-_]+/g," ").trim().replace(/\s+/g," "); process.stdout.write(value.replace(/\b\w/g,(m)=>m.toUpperCase()));' "$1"
}

json_string() {
  node -e 'process.stdout.write(JSON.stringify(process.argv[1] ?? ""));' "$1"
}

json_boolean() {
  if [[ "$1" == "true" ]]; then
    printf 'true'
  else
    printf 'false'
  fi
}

read_flag_value() {
  local index="$1"
  local flag="$2"
  local value="${ARGS[$index]:-}"
  [[ -n "$value" && "$value" != --* ]] || fail "$flag requires a value."
  printf '%s' "$value"
}

prompt_text() {
  local label="$1"
  local default_value="${2:-}"
  local required="${3:-0}"
  local value

  while true; do
    if [[ -n "$default_value" ]]; then
      read -r -p "$label ($default_value): " value || exit 1
    else
      read -r -p "$label: " value || exit 1
    fi
    value="$(trim "${value:-}")"
    if [[ -z "$value" ]]; then
      value="$default_value"
    fi
    if [[ "$required" == "1" && -z "$value" ]]; then
      echo "A value is required."
      continue
    fi
    printf '%s' "$value"
    return
  done
}

prompt_confirm() {
  local label="$1"
  local default_value="${2:-false}"
  local hint="y/N"
  local value

  [[ "$default_value" == "true" ]] && hint="Y/n"

  while true; do
    read -r -p "$label ($hint): " value || exit 1
    value="$(trim "${value:-}")"
    value="${value,,}"
    if [[ -z "$value" ]]; then
      printf '%s' "$default_value"
      return
    fi
    case "$value" in
      y|yes) printf 'true'; return ;;
      n|no) printf 'false'; return ;;
    esac
    echo "Please answer yes or no."
  done
}

prompt_choice() {
  local label="$1"
  local default_value="$2"
  shift 2
  local choices=("$@")
  local value

  echo "$label"
  for value in "${choices[@]}"; do
    echo "  - $value"
  done

  while true; do
    value="$(prompt_text "Choose one" "$default_value" 1)"
    for choice in "${choices[@]}"; do
      if [[ "$value" == "$choice" ]]; then
        printf '%s' "$value"
        return
      fi
    done
    echo "Choose one of: ${choices[*]}"
  done
}

normalize_search_config() {
  local raw="$1"
  node - "$raw" <<'NODE'
const fs = require("node:fs");
let raw = process.argv[2] || "";
if (raw.startsWith("@")) {
  raw = fs.readFileSync(raw.slice(1), "utf8");
}
const parsed = JSON.parse(raw);
const object = Array.isArray(parsed) ? { tables: parsed } : parsed;
if (!object || typeof object !== "object" || Array.isArray(object)) {
  throw new Error("Search config must be an object or array.");
}
const tables = Array.isArray(object.tables) ? object.tables : [];
const normalizedTables = tables.map((entry, index) => {
  if (!entry || typeof entry !== "object" || Array.isArray(entry)) {
    throw new Error(`Search table at index ${index} must be an object.`);
  }
  const tableName = typeof entry.tableName === "string" ? entry.tableName.trim() : "";
  const label = typeof entry.label === "string" && entry.label.trim() ? entry.label.trim() : tableName;
  const searchFields = Array.isArray(entry.searchFields)
    ? entry.searchFields.map((field) => String(field).trim()).filter(Boolean)
    : [];
  if (!tableName) {
    throw new Error(`Search table at index ${index} is missing tableName.`);
  }
  if (searchFields.length === 0) {
    throw new Error(`Search table "${tableName}" must include at least one search field.`);
  }
  return {
    tableName,
    label,
    searchFields,
    displayField: typeof entry.displayField === "string" ? entry.displayField.trim() : "",
    subtitleField: typeof entry.subtitleField === "string" ? entry.subtitleField.trim() : "",
    hrefTemplate:
      typeof entry.hrefTemplate === "string" && entry.hrefTemplate.trim()
        ? entry.hrefTemplate.trim()
        : `/records/${encodeURIComponent(tableName)}/:id`,
  };
});
let files = null;
if (object.files != null) {
  if (!object.files || typeof object.files !== "object" || Array.isArray(object.files)) {
    throw new Error("Search files config must be an object.");
  }
  files = {
    enabled: object.files.enabled !== false,
    label:
      typeof object.files.label === "string" && object.files.label.trim()
        ? object.files.label.trim()
        : "Files",
    hrefTemplate:
      typeof object.files.hrefTemplate === "string" && object.files.hrefTemplate.trim()
        ? object.files.hrefTemplate.trim()
        : "/files?path=:path",
  };
}
if (normalizedTables.length === 0 && !(files && files.enabled)) {
  throw new Error("Search config must include at least one table or enabled files.");
}
process.stdout.write(JSON.stringify({ tables: normalizedTables, files }));
NODE
}

pretty_json() {
  node -e 'process.stdout.write(JSON.stringify(JSON.parse(process.argv[1]), null, 2));' "$1"
}

write_file() {
  local path="$1"
  shift
  mkdir -p "$(dirname "$path")"
  cat >"$path"
}

prepare_registry_item() {
  local item_name="$1"
  local output_path="$2"
  local remote_item="${LEMMA_REGISTRY_URL/\{name\}/$item_name}"

  curl -fsSL "$remote_item" | node -e '
    const fs = require("node:fs");
    const output = process.argv[1];
    const itemName = process.argv[2];
    const json = JSON.parse(fs.readFileSync(0, "utf8"));
    if (itemName !== "lemma-ui") {
      json.registryDependencies = [];
    }
    fs.writeFileSync(output, JSON.stringify(json, null, 2));
  ' "$output_path" "$item_name"
}

install_registry_item() {
  local app_root="$1"
  local item_name="$2"
  local temp_stub
  local temp_json
  temp_stub="$(mktemp "/tmp/${item_name}.XXXXXX")"
  temp_json="${temp_stub}.json"
  prepare_registry_item "$item_name" "$temp_json"
  (
    cd "$app_root"
    npx shadcn@latest add "$temp_json" -y --overwrite
  )
  rm -f "$temp_json" "$temp_stub"
}

ensure_fresh_directory() {
  local dir="$1"
  if [[ ! -e "$dir" ]]; then
    return
  fi
  if [[ -n "$(find "$dir" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]]; then
    fail "$dir is not empty. Use a fresh directory."
  fi
}

APP_ROOT=""
SEARCH_BLOCK_ENABLED="false"
ASSISTANT_BLOCK_ENABLED="false"
ASSISTANT_PAGE_ENABLED="false"
MEMBERS_BLOCK_ENABLED="false"
APP_TITLE_JSON=""

ARGS=("$@")
INDEX=0
while [[ $INDEX -lt ${#ARGS[@]} ]]; do
  TOKEN="${ARGS[$INDEX]}"
  case "$TOKEN" in
    -h|--help)
      usage
      exit 0
      ;;
    --desk-name)
      INDEX=$((INDEX + 1))
      DESK_NAME="$(read_flag_value "$INDEX" "--desk-name")"
      ;;
    --pod-id)
      INDEX=$((INDEX + 1))
      POD_ID="$(read_flag_value "$INDEX" "--pod-id")"
      ;;
    --title)
      INDEX=$((INDEX + 1))
      APP_TITLE="$(read_flag_value "$INDEX" "--title")"
      ;;
    --nav)
      INDEX=$((INDEX + 1))
      NAVIGATION="$(read_flag_value "$INDEX" "--nav")"
      ;;
    --search)
      SEARCH_ENABLED="true"
      ;;
    --no-search)
      SEARCH_ENABLED="false"
      ;;
    --search-config)
      INDEX=$((INDEX + 1))
      SEARCH_CONFIG_RAW="$(read_flag_value "$INDEX" "--search-config")"
      ;;
    --assistant)
      ASSISTANT_ENABLED="true"
      if [[ $((INDEX + 1)) -lt ${#ARGS[@]} ]]; then
        NEXT_TOKEN="${ARGS[$((INDEX + 1))]}"
        if [[ -n "$NEXT_TOKEN" && "$NEXT_TOKEN" != --* ]]; then
          INDEX=$((INDEX + 1))
          ASSISTANT_NAME="$NEXT_TOKEN"
          if [[ $((INDEX + 1)) -lt ${#ARGS[@]} ]]; then
            NEXT_TOKEN="${ARGS[$((INDEX + 1))]}"
            if [[ -n "$NEXT_TOKEN" && "$NEXT_TOKEN" != --* ]]; then
              INDEX=$((INDEX + 1))
              ASSISTANT_MODE="$NEXT_TOKEN"
            fi
          fi
        fi
      fi
      ;;
    --no-assistant)
      ASSISTANT_ENABLED="false"
      ;;
    --members)
      MEMBERS_ENABLED="true"
      ;;
    --no-members)
      MEMBERS_ENABLED="false"
      ;;
    --theme-toggle)
      THEME_TOGGLE_ENABLED="true"
      ;;
    --no-theme-toggle)
      THEME_TOGGLE_ENABLED="false"
      ;;
    --assistant-name)
      INDEX=$((INDEX + 1))
      ASSISTANT_NAME="$(read_flag_value "$INDEX" "--assistant-name")"
      ;;
    --assistant-mode)
      INDEX=$((INDEX + 1))
      ASSISTANT_MODE="$(read_flag_value "$INDEX" "--assistant-mode")"
      ;;
    -y|--yes)
      AUTO_YES="1"
      ;;
    --*)
      fail "Unknown option $TOKEN"
      ;;
    *)
      if [[ "$TARGET_DIR" != "." ]]; then
        fail "Unexpected argument $TOKEN"
      fi
      TARGET_DIR="$TOKEN"
      ;;
  esac
  INDEX=$((INDEX + 1))
done

require_command npm
require_command npx
require_command node

INTERACTIVE="0"
if [[ -t 0 && -t 1 && "$AUTO_YES" != "1" ]]; then
  INTERACTIVE="1"
fi

if [[ -n "$DESK_NAME" && "$TARGET_DIR" == "." ]]; then
  TARGET_DIR="$DESK_NAME"
fi

if [[ "$TARGET_DIR" = /* ]]; then
  APP_ROOT="$TARGET_DIR"
else
  APP_ROOT="$PWD/$TARGET_DIR"
fi
TITLE_SOURCE="$DESK_NAME"
if [[ -z "$TITLE_SOURCE" ]]; then
  TITLE_SOURCE="$(basename "$APP_ROOT")"
fi
DEFAULT_TITLE="$(humanize_name "$TITLE_SOURCE")"

if [[ -n "$NAVIGATION" ]]; then
  NAVIGATION="$(normalize_nav "$NAVIGATION")"
fi
if [[ -z "$NAVIGATION" ]]; then
  if [[ "$INTERACTIVE" == "1" ]]; then
    NAVIGATION="$(prompt_choice "Choose a shell scaffold" "sidebar" "sidebar" "topbar" "single-page")"
  else
    NAVIGATION="sidebar"
  fi
fi
[[ -n "$(normalize_nav "$NAVIGATION")" ]] || fail "Unsupported navigation type: $NAVIGATION"
NAVIGATION="$(normalize_nav "$NAVIGATION")"

if [[ -z "$APP_TITLE" ]]; then
  if [[ -n "$DESK_NAME" ]]; then
    APP_TITLE="$DEFAULT_TITLE"
  elif [[ "$INTERACTIVE" == "1" ]]; then
    APP_TITLE="$(prompt_text "App title" "$DEFAULT_TITLE" 1)"
  else
    APP_TITLE="$DEFAULT_TITLE"
  fi
fi

if [[ -z "$POD_ID" ]]; then
  if [[ "$INTERACTIVE" == "1" ]]; then
    POD_ID="$(prompt_text "Pod ID" "" 1)"
  else
    fail "A pod id is required."
  fi
fi

if [[ -z "$SEARCH_ENABLED" ]]; then
  if [[ "$INTERACTIVE" == "1" ]]; then
    SEARCH_ENABLED="$(prompt_confirm "Enable global search?" "false")"
  else
    SEARCH_ENABLED="false"
  fi
fi

if [[ "$SEARCH_ENABLED" == "true" ]]; then
  if [[ -z "$SEARCH_CONFIG_RAW" ]]; then
    if [[ "$INTERACTIVE" == "1" ]]; then
      echo 'Record search sources JSON example: [{"tableName":"issues","label":"Issues","searchFields":["identifier","title","status"],"displayField":"title","subtitleField":"status"}]'
      TABLES_JSON="$(prompt_text "Record search sources JSON" "" 1)"
      ENABLE_FILES="$(prompt_confirm "Enable file search too?" "false")"
      if [[ "$ENABLE_FILES" == "true" ]]; then
        FILE_LABEL="$(prompt_text "File search label" "Files" 1)"
        FILE_ROUTE="$(prompt_text "File result route template" "/files?path=:path" 1)"
        SEARCH_CONFIG_RAW="$(node -e 'const tables=JSON.parse(process.argv[1]); const label=process.argv[2]; const hrefTemplate=process.argv[3]; process.stdout.write(JSON.stringify({tables, files:{enabled:true,label,hrefTemplate}}));' "$TABLES_JSON" "$FILE_LABEL" "$FILE_ROUTE")"
      else
        SEARCH_CONFIG_RAW="$(node -e 'const tables=JSON.parse(process.argv[1]); process.stdout.write(JSON.stringify({tables}));' "$TABLES_JSON")"
      fi
    else
      fail "Search is enabled, but no --search-config was provided."
    fi
  fi
  SEARCH_CONFIG_JSON="$(normalize_search_config "$SEARCH_CONFIG_RAW")" || fail "Invalid search config."
  SEARCH_BLOCK_ENABLED="true"
fi

if [[ -z "$ASSISTANT_ENABLED" ]]; then
  if [[ "$INTERACTIVE" == "1" ]]; then
    ASSISTANT_ENABLED="$(prompt_confirm "Enable assistant scaffolding?" "false")"
  else
    ASSISTANT_ENABLED="false"
  fi
fi

if [[ "$ASSISTANT_ENABLED" == "true" ]]; then
  if [[ -z "$ASSISTANT_NAME" ]]; then
    if [[ "$INTERACTIVE" == "1" ]]; then
      ASSISTANT_NAME="$(prompt_text "Assistant name" "" 1)"
    else
      fail "Assistant scaffolding needs --assistant-name."
    fi
  fi

  if [[ -n "$ASSISTANT_MODE" ]]; then
    ASSISTANT_MODE="$(normalize_assistant_mode "$ASSISTANT_MODE")"
  fi
  if [[ -z "$ASSISTANT_MODE" ]]; then
    if [[ "$INTERACTIVE" == "1" ]]; then
      ASSISTANT_MODE="$(prompt_choice "Choose an assistant surface" "page" "page" "popup" "right-sidebar")"
    else
      ASSISTANT_MODE="page"
    fi
  fi
  [[ -n "$(normalize_assistant_mode "$ASSISTANT_MODE")" ]] || fail "Unsupported assistant mode: $ASSISTANT_MODE"
  ASSISTANT_MODE="$(normalize_assistant_mode "$ASSISTANT_MODE")"

  ASSISTANT_BLOCK_ENABLED="true"
  if [[ "$ASSISTANT_MODE" == "page" ]]; then
    ASSISTANT_PAGE_ENABLED="true"
  else
    ASSISTANT_PAGE_ENABLED="false"
  fi
  ASSISTANT_CONFIG_JSON="$(node -e 'const assistantName=process.argv[1]; const mode=process.argv[2]; const pageEnabled=mode === "page"; process.stdout.write(JSON.stringify({assistantName, mode, pageEnabled}));' "$ASSISTANT_NAME" "$ASSISTANT_MODE")"
fi

if [[ -z "$MEMBERS_ENABLED" ]]; then
  if [[ "$INTERACTIVE" == "1" ]]; then
    MEMBERS_ENABLED="$(prompt_confirm "Add a members page?" "false")"
  else
    MEMBERS_ENABLED="false"
  fi
fi

if [[ "$MEMBERS_ENABLED" == "true" ]]; then
  MEMBERS_BLOCK_ENABLED="true"
fi

APP_TITLE_JSON="$(json_string "$APP_TITLE")"
POD_ID_JSON="$(json_string "$POD_ID")"
ASSISTANT_NAME_JSON="$(json_string "$ASSISTANT_NAME")"
SEARCH_CONFIG_TS="$SEARCH_CONFIG_JSON"
ASSISTANT_CONFIG_TS="$ASSISTANT_CONFIG_JSON"

ensure_fresh_directory "$APP_ROOT"

TARGET_PARENT="$(dirname "$APP_ROOT")"
TARGET_NAME="$(basename "$APP_ROOT")"
mkdir -p "$TARGET_PARENT"

echo "Creating Vite React + TypeScript scaffold..."
if [[ -e "$APP_ROOT" ]]; then
  (
    cd "$APP_ROOT"
    npm_config_yes=true npm create vite@latest . -- --template react-ts
  )
else
  (
    cd "$TARGET_PARENT"
    npm_config_yes=true npm create vite@latest "$TARGET_NAME" -- --template react-ts
  )
fi

echo "Installing app dependencies..."
(
  cd "$APP_ROOT"
  npm install
  npm install lemma-sdk react-router-dom
  npm install -D tailwindcss @tailwindcss/vite
)

rm -rf "$APP_ROOT/src/assets"
rm -f "$APP_ROOT/public/vite.svg" "$APP_ROOT/src/App.css"

mkdir -p "$APP_ROOT/src/lib" "$APP_ROOT/src/components/app"

COMPONENTS_JSON_CONTENT="$(cat <<EOF
{
  "\$schema": "$SHADCN_SCHEMA_URL",
  "style": "base-nova",
  "rsc": false,
  "tsx": true,
  "tailwind": {
    "config": "",
    "css": "src/index.css",
    "baseColor": "neutral",
    "cssVariables": true,
    "prefix": ""
  },
  "iconLibrary": "lucide",
  "aliases": {
    "components": "@/components",
    "ui": "@/components/lemma/ui",
    "utils": "@/components/lemma/lib/utils",
    "lib": "@/lib",
    "hooks": "@/hooks"
  },
  "registries": {
    "@lemma": "$LEMMA_REGISTRY_URL"
  }
}
EOF
)"
write_file "$APP_ROOT/components.json" <<<"$COMPONENTS_JSON_CONTENT"

write_file "$APP_ROOT/vite.config.ts" <<'EOF'
import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
});
EOF

write_file "$APP_ROOT/src/index.css" <<'EOF'
@import url("https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=Source+Code+Pro:wght@400;500;600&display=swap");
@import "tailwindcss";

@theme inline {
  --font-sans: "IBM Plex Sans", ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  --font-heading: var(--font-sans);
  --font-mono: "Source Code Pro", ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
  --color-background: var(--background);
  --color-foreground: var(--foreground);
  --color-card: var(--card);
  --color-card-foreground: var(--card-foreground);
  --color-popover: var(--popover);
  --color-popover-foreground: var(--popover-foreground);
  --color-primary: var(--primary);
  --color-primary-foreground: var(--primary-foreground);
  --color-secondary: var(--secondary);
  --color-secondary-foreground: var(--secondary-foreground);
  --color-muted: var(--muted);
  --color-muted-foreground: var(--muted-foreground);
  --color-accent: var(--accent);
  --color-accent-foreground: var(--accent-foreground);
  --color-destructive: var(--destructive);
  --color-border: var(--border);
  --color-input: var(--input);
  --color-ring: var(--ring);
  --radius-sm: 0.25rem;
  --radius-md: 0.375rem;
  --radius-lg: var(--radius);
  --radius-xl: 0.75rem;
}

:root {
  color-scheme: light;
  --background: #fdfdf8;
  --foreground: #23251d;
  --card: #ffffff;
  --card-foreground: #23251d;
  --popover: rgba(255, 255, 255, 0.96);
  --popover-foreground: #23251d;
  --primary: #1e1f23;
  --primary-foreground: #ffffff;
  --secondary: #eeefe9;
  --secondary-foreground: #4d4f46;
  --muted: #e5e7e0;
  --muted-foreground: #65675e;
  --accent: #eeefe9;
  --accent-foreground: #23251d;
  --destructive: #f54e00;
  --border: #d3d6cc;
  --input: #c8cbc0;
  --ring: #3b82f6;
  --radius: 0.625rem;
  --brand-accent: #f54e00;
  --brand-warm: #f7a501;
  --surface-quiet: #eeefe9;
  --surface-muted: #e5e7e0;
  --text-secondary: #4d4f46;
  --text-tertiary: #65675e;
  --text-soft: #9ea096;
  --shadow-xs: 0 1px 2px rgba(35, 37, 29, 0.04);
  --shadow-sm: 0 8px 24px rgba(35, 37, 29, 0.05);
  --shadow-lg: 0 24px 60px rgba(35, 37, 29, 0.12);
}

.dark {
  color-scheme: dark;
  --background: #151614;
  --foreground: #e5e7e0;
  --card: #1c1d1a;
  --card-foreground: #e5e7e0;
  --popover: rgba(28, 29, 26, 0.96);
  --popover-foreground: #e5e7e0;
  --primary: #fdfdf8;
  --primary-foreground: #23251d;
  --secondary: #252621;
  --secondary-foreground: #c7cbbf;
  --muted: #2e2f2a;
  --muted-foreground: #9ea096;
  --accent: #252621;
  --accent-foreground: #e5e7e0;
  --destructive: #f54e00;
  --border: #32332e;
  --input: #3d3f38;
  --ring: #4e93e8;
  --brand-accent: #f54e00;
  --brand-warm: #f7a501;
  --surface-quiet: #252621;
  --surface-muted: #2e2f2a;
  --text-secondary: #c7cbbf;
  --text-tertiary: #9ea096;
  --text-soft: #65675e;
  --shadow-xs: 0 1px 2px rgba(0, 0, 0, 0.2);
  --shadow-sm: 0 12px 32px rgba(0, 0, 0, 0.22);
  --shadow-lg: 0 28px 72px rgba(0, 0, 0, 0.34);
}

@layer base {
  * {
    @apply border-border;
  }

  html,
  body,
  #root {
    min-height: 100%;
  }

  html {
    background: var(--background);
  }

  body {
    @apply font-sans text-foreground antialiased;
    background-color: var(--background);
    background-image:
      radial-gradient(circle at top, rgba(245, 78, 0, 0.07), transparent 28%),
      radial-gradient(circle at bottom left, rgba(30, 31, 35, 0.04), transparent 24%);
    letter-spacing: -0.01em;
  }

  h1,
  h2,
  h3,
  h4,
  h5,
  h6 {
    font-family: var(--font-heading);
    color: var(--foreground);
    letter-spacing: -0.02em;
  }

  h1 {
    font-size: clamp(1.875rem, 3vw, 2.25rem);
    line-height: 1.15;
    font-weight: 700;
  }

  h2 {
    font-size: clamp(1.5rem, 2.4vw, 1.875rem);
    line-height: 1.2;
    font-weight: 700;
  }

  h3 {
    font-size: 1.25rem;
    line-height: 1.35;
    font-weight: 600;
  }

  p,
  label,
  li,
  input,
  textarea,
  select,
  button {
    letter-spacing: -0.01em;
  }

  a {
    color: var(--brand-accent);
    text-decoration-color: color-mix(in srgb, var(--brand-accent) 35%, transparent);
  }

  a:hover {
    color: var(--brand-warm);
  }

  code,
  kbd,
  samp,
  pre {
    font-family: var(--font-mono);
  }

  ::selection {
    background: color-mix(in srgb, var(--brand-accent) 22%, white);
    color: var(--foreground);
  }

  [data-slot="card"] {
    box-shadow: var(--shadow-xs);
  }

  [data-slot="input"],
  textarea,
  select {
    background: var(--surface-quiet);
  }

  button:focus-visible,
  input:focus-visible,
  textarea:focus-visible,
  select:focus-visible {
    outline: 2px solid color-mix(in srgb, var(--ring) 60%, transparent);
    outline-offset: 2px;
  }
}
EOF

write_file "$APP_ROOT/src/lib/runtime-config.ts" <<'EOF'
export const runtimeConfig = {
  podId: import.meta.env.VITE_LEMMA_POD_ID ?? "",
  assistantName: import.meta.env.VITE_LEMMA_ASSISTANT_NAME ?? "",
};

export const hasPodId = runtimeConfig.podId.trim().length > 0;
EOF

write_file "$APP_ROOT/src/lib/client.ts" <<'EOF'
import { LemmaClient } from "lemma-sdk";
import { runtimeConfig } from "@/lib/runtime-config";

export const client = new LemmaClient({
  ...(runtimeConfig.podId ? { podId: runtimeConfig.podId } : {}),
});
EOF

APP_CONFIG_CONTENT="$(cat <<EOF
export type NavigationType = "single-page" | "sidebar" | "topbar";

export interface SearchTableConfig {
  tableName: string;
  label: string;
  searchFields: string[];
  displayField: string;
  subtitleField: string;
  hrefTemplate: string;
}

export interface SearchFilesConfig {
  enabled: boolean;
  label: string;
  hrefTemplate: string;
}

export interface SearchConfig {
  tables: SearchTableConfig[];
  files: SearchFilesConfig | null;
}

export interface AssistantConfig {
  assistantName: string;
  mode: "page" | "popup" | "right-sidebar";
  pageEnabled: boolean;
}

export const appConfig: {
  title: string;
  navigation: NavigationType;
  search: SearchConfig | null;
  assistant: AssistantConfig | null;
  membersPage: boolean;
  themeToggle: boolean;
} = {
  title: $APP_TITLE_JSON,
  navigation: $(json_string "$NAVIGATION"),
  search: $SEARCH_CONFIG_TS,
  assistant: $ASSISTANT_CONFIG_TS,
  membersPage: $(json_boolean "$MEMBERS_BLOCK_ENABLED"),
  themeToggle: $(json_boolean "$THEME_TOGGLE_ENABLED"),
};
EOF
)"
write_file "$APP_ROOT/src/app-config.ts" <<<"$APP_CONFIG_CONTENT"

write_file "$APP_ROOT/src/App.tsx" <<'EOF'
import * as React from "react";
import {
  HashRouter,
  NavLink,
  Navigate,
  Route,
  Routes,
  useLocation,
  useParams,
  useSearchParams,
} from "react-router-dom";
import { Bot, Sparkles, Users } from "lucide-react";
import { AuthGuard } from "lemma-sdk/react";
import { Button } from "@/components/lemma/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/lemma/ui/card";
import { cn } from "@/components/lemma/lib/utils";
import {
  AssistantPage,
  AssistantPopup,
  AssistantRail,
} from "@/components/app/assistant-entry";
import { MembersPage } from "@/components/app/members-entry";
import { ProfileMenu } from "@/components/app/profile-menu";
import { SearchControl, SidebarSearchButton } from "@/components/app/search-entry";
import { ThemeToggle } from "@/components/app/theme-toggle";
import { appConfig } from "@/app-config";
import { client } from "@/lib/client";
import { hasPodId } from "@/lib/runtime-config";

export default function App() {
  return (
    <HashRouter>
      <AuthGuard client={client} loadingFallback={<BootFallback />}>
        <DeskShell />
      </AuthGuard>
    </HashRouter>
  );
}

function DeskShell() {
  const location = useLocation();
  const showAssistantRail =
    appConfig.assistant?.mode === "right-sidebar" &&
    location.pathname !== "/assistant";
  const assistantRail = showAssistantRail ? <AssistantRail /> : null;

  if (appConfig.navigation === "single-page") {
    return (
      <ShellFrame sidebar={null} topbar={null} assistantRail={assistantRail}>
        <DeskContent />
        {appConfig.assistant?.mode === "popup" ? <AssistantPopup /> : null}
      </ShellFrame>
    );
  }

  if (appConfig.navigation === "topbar") {
    return (
      <ShellFrame sidebar={null} topbar={<TopbarNav />} assistantRail={assistantRail}>
        <DeskContent />
        {appConfig.assistant?.mode === "popup" ? <AssistantPopup /> : null}
      </ShellFrame>
    );
  }

  return (
    <ShellFrame sidebar={<SidebarNav />} topbar={null} assistantRail={assistantRail}>
      <DeskContent />
      {appConfig.assistant?.mode === "popup" ? <AssistantPopup /> : null}
    </ShellFrame>
  );
}

function ShellFrame({
  sidebar,
  topbar,
  assistantRail,
  children,
}: {
  sidebar: React.ReactNode;
  topbar: React.ReactNode;
  assistantRail: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="flex h-screen flex-col overflow-hidden bg-background text-foreground">
      {topbar}
      <div
        className={cn(
          "min-h-0 flex-1",
          sidebar
            ? "mx-auto grid w-full max-w-[1600px] lg:grid-cols-[252px_minmax(0,1fr)]"
            : "mx-auto flex w-full max-w-[1440px] flex-col",
        )}
      >
        {sidebar ? (
          <aside className="hidden border-r border-border/70 bg-card/70 lg:sticky lg:top-0 lg:flex lg:h-screen lg:flex-col lg:overflow-hidden">
            {sidebar}
          </aside>
        ) : null}
        <div className="flex min-h-0 flex-1 flex-col">
          <div
            className={cn(
              "flex min-h-0 flex-1",
              assistantRail &&
                "xl:grid xl:grid-cols-[minmax(0,1fr)_minmax(22rem,26rem)]",
            )}
          >
            <div className="flex min-h-0 min-w-0 flex-1 flex-col">{children}</div>
            {assistantRail}
          </div>
        </div>
      </div>
    </div>
  );
}

function navItems() {
  const items: Array<{ to: string; label: string; icon: typeof Sparkles | typeof Bot | typeof Users }> = [
    { to: "/", label: "Page1", icon: Sparkles },
  ];

  if (appConfig.assistant?.pageEnabled) {
    items.push({ to: "/assistant", label: "Assistant", icon: Bot });
  }

  if (appConfig.membersPage) {
    items.push({ to: "/members", label: "Members", icon: Users });
  }

  return items;
}

function routeTitle(pathname: string) {
  if (pathname === "/assistant") return "Assistant";
  if (pathname === "/members") return "Members";
  return "Page1";
}

function useHeaderState() {
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const isAssistantPage = location.pathname === "/assistant";
  const title = routeTitle(location.pathname);
  const showAssistantNav = searchParams.get("assistant-nav") === "1";

  const toggleAssistantNav = () => {
    const next = new URLSearchParams(searchParams);
    if (showAssistantNav) {
      next.delete("assistant-nav");
    } else {
      next.set("assistant-nav", "1");
    }
    setSearchParams(next);
  };

  return {
    location,
    title,
    isAssistantPage,
    showAssistantNav,
    toggleAssistantNav,
  };
}

function HeaderActions({
  includeProfile = false,
  showThemeToggle = false,
}: {
  includeProfile?: boolean;
  showThemeToggle?: boolean;
}) {
  const { isAssistantPage, showAssistantNav, toggleAssistantNav } = useHeaderState();

  return (
    <div className="flex flex-wrap items-center gap-2">
      {isAssistantPage && appConfig.assistant ? (
        <Button variant="outline" onClick={toggleAssistantNav}>
          {showAssistantNav ? "Hide conversations" : "Show conversations"}
        </Button>
      ) : null}
      {appConfig.search ? <SearchControl /> : null}
      {appConfig.assistant?.pageEnabled && appConfig.navigation === "single-page" ? (
        <NavLink to="/assistant">
          <Button variant="outline">
            <Bot data-icon="inline-start" />
            Assistant
          </Button>
        </NavLink>
      ) : null}
      {showThemeToggle ? <ThemeToggle /> : null}
      {includeProfile ? <ProfileMenu /> : null}
    </div>
  );
}

function TopbarNav() {
  const { title } = useHeaderState();

  return (
    <header className="border-b border-border/70 bg-card/80 backdrop-blur">
      <div className="mx-auto flex max-w-[1440px] items-center justify-between gap-4 px-4 py-3 md:px-6">
        <div className="flex min-w-0 items-center gap-4">
          <div className="flex items-center gap-3">
            <div className="flex size-10 items-center justify-center rounded-2xl bg-primary text-primary-foreground">
              <Sparkles className="size-5" />
            </div>
            <div>
              <p className="text-sm font-semibold">{appConfig.title}</p>
              <p className="text-xs text-muted-foreground">{title}</p>
            </div>
          </div>
          <nav className="flex items-center gap-2">
            {navItems().map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  cn(
                    "rounded-xl px-3 py-2 text-sm text-muted-foreground transition-colors",
                    isActive
                      ? "bg-primary/10 font-medium text-foreground"
                      : "hover:bg-muted hover:text-foreground",
                  )
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
        </div>
        <HeaderActions includeProfile showThemeToggle={appConfig.themeToggle} />
      </div>
    </header>
  );
}

function SidebarNav() {
  return (
    <div className="flex h-full min-h-0 flex-col p-4">
      <div className="flex min-h-0 flex-1 flex-col gap-6">
        <div className="flex items-center gap-3">
          <div className="flex size-10 items-center justify-center rounded-2xl bg-primary text-primary-foreground">
            <Sparkles className="size-5" />
          </div>
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold">{appConfig.title}</p>
            <p className="truncate text-xs text-muted-foreground">Lemma desk scaffold</p>
          </div>
        </div>

        <nav className="grid gap-1">
          {navItems().map((item) => {
            const Icon = item.icon;
            return (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  cn(
                    "flex items-center gap-3 rounded-xl px-3 py-2 text-sm text-muted-foreground transition-colors",
                    isActive
                      ? "bg-primary/10 font-medium text-foreground"
                      : "hover:bg-muted hover:text-foreground",
                  )
                }
              >
                <Icon className="size-4" />
                <span>{item.label}</span>
              </NavLink>
            );
          })}
        </nav>

        <div className="mt-auto flex flex-col gap-3">
          {appConfig.search ? <SidebarSearchButton /> : null}
          {appConfig.themeToggle ? <ThemeToggle sidebar /> : null}
          <ProfileMenu sidebar />
        </div>
      </div>
    </div>
  );
}

function DeskContent() {
  const location = useLocation();
  const flushContent = location.pathname === "/assistant";
  const showPageHeader = appConfig.navigation !== "topbar";

  return (
    <>
      {showPageHeader ? <PageHeader /> : null}
      <main className="min-h-0 flex-1 overflow-auto">
        <div
          className={cn(
            "flex h-full w-full flex-col",
            flushContent
              ? "min-h-0"
              : "mx-auto max-w-[1440px] gap-6 px-4 py-4 md:px-6 md:py-6",
          )}
        >
          {!hasPodId ? (
            <div className={cn(flushContent ? "px-4 py-4 md:px-6 md:py-6" : undefined)}>
              <RuntimeWarning />
            </div>
          ) : null}
          <Routes>
            <Route path="/" element={<WorkspacePage />} />
            <Route path="/records/:tableName/:recordId" element={<RecordPlaceholderPage />} />
            <Route path="/files" element={<FilePlaceholderPage />} />
            {appConfig.assistant?.pageEnabled ? (
              <Route path="/assistant" element={<AssistantPage />} />
            ) : null}
            {appConfig.membersPage ? (
              <Route path="/members" element={<MembersPage />} />
            ) : null}
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </div>
      </main>
    </>
  );
}

function PageHeader() {
  const { title } = useHeaderState();

  return (
    <header className="sticky top-0 z-20 border-b border-border/70 bg-background/92 backdrop-blur">
      <div className="mx-auto flex max-w-[1440px] flex-col gap-3 px-4 py-3 md:px-6">
        <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
          <div className="min-w-0">
            <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">
              Lemma
            </p>
            <h1 className="truncate text-lg font-semibold">{title}</h1>
          </div>
          <HeaderActions
            includeProfile={appConfig.navigation === "single-page"}
            showThemeToggle={appConfig.navigation === "single-page" && appConfig.themeToggle}
          />
        </div>
      </div>
    </header>
  );
}

function RuntimeWarning() {
  return (
    <Card className="border-amber-500/30 bg-amber-500/10">
      <CardHeader>
        <CardTitle className="text-base">Pod connection missing</CardTitle>
        <CardDescription>
          Set VITE_LEMMA_POD_ID in .env.local to make the scaffold interactive.
        </CardDescription>
      </CardHeader>
    </Card>
  );
}

function PageIntro({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  return (
    <div className="space-y-1">
      <h2 className="text-base font-semibold">{title}</h2>
      <p className="text-sm text-muted-foreground">{description}</p>
    </div>
  );
}

function WorkspacePage() {
  return (
    <section className="flex min-h-[16rem] items-start">
      <div className="rounded-xl border border-dashed border-border/80 bg-card/70 px-5 py-4 text-sm text-muted-foreground shadow-none">
        Add components here and create new pages as required.
      </div>
    </section>
  );
}

function RecordPlaceholderPage() {
  const params = useParams();

  return (
    <section className="grid gap-4">
      <PageIntro
        title="Record route placeholder"
        description="Global search already points here by default. Replace this route with your actual record detail surface."
      />
      <Card className="border-border/70 shadow-none">
        <CardContent className="space-y-2 p-6 text-sm text-muted-foreground">
          <p>
            Table: <span className="font-mono text-foreground">{params.tableName}</span>
          </p>
          <p>
            Record: <span className="font-mono text-foreground">{params.recordId}</span>
          </p>
        </CardContent>
      </Card>
    </section>
  );
}

function FilePlaceholderPage() {
  const [searchParams] = useSearchParams();
  const filePath = searchParams.get("path") ?? "";

  return (
    <section className="grid gap-4">
      <PageIntro
        title="File route placeholder"
        description="Wire this route to your document workspace or file browser when you are ready."
      />
      <Card className="border-border/70 shadow-none">
        <CardContent className="space-y-2 p-6 text-sm text-muted-foreground">
          <p>
            Path: <span className="font-mono text-foreground">{filePath || "unset"}</span>
          </p>
        </CardContent>
      </Card>
    </section>
  );
}

function BootFallback() {
  return (
    <main className="flex min-h-screen items-center justify-center p-6 text-sm text-muted-foreground">
      Loading desk...
    </main>
  );
}
EOF

write_file "$APP_ROOT/src/main.tsx" <<'EOF'
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
EOF

write_file "$APP_ROOT/src/vite-env.d.ts" <<'EOF'
/// <reference types="vite/client" />
EOF

write_file "$APP_ROOT/src/components/app/theme-toggle.tsx" <<'EOF'
import * as React from "react";
import { Moon, Sun } from "lucide-react";
import { Button } from "@/components/lemma/ui/button";

type ThemeMode = "light" | "dark";

const STORAGE_KEY = "lemma-theme";

function resolveInitialTheme(): ThemeMode {
  if (typeof window === "undefined") return "light";
  const stored = window.localStorage.getItem(STORAGE_KEY);
  if (stored === "light" || stored === "dark") return stored;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function applyTheme(theme: ThemeMode) {
  if (typeof document === "undefined") return;
  document.documentElement.classList.toggle("dark", theme === "dark");
  document.documentElement.style.colorScheme = theme;
}

export function ThemeToggle({ sidebar = false }: { sidebar?: boolean }) {
  const [theme, setTheme] = React.useState<ThemeMode>(() => resolveInitialTheme());

  React.useEffect(() => {
    applyTheme(theme);
    if (typeof window !== "undefined") {
      window.localStorage.setItem(STORAGE_KEY, theme);
    }
  }, [theme]);

  React.useEffect(() => {
    if (typeof window === "undefined") return undefined;
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const handleChange = () => {
      const stored = window.localStorage.getItem(STORAGE_KEY);
      if (stored === "light" || stored === "dark") return;
      setTheme(media.matches ? "dark" : "light");
    };
    media.addEventListener("change", handleChange);
    return () => media.removeEventListener("change", handleChange);
  }, []);

  const nextTheme: ThemeMode = theme === "dark" ? "light" : "dark";

  return (
    <Button
      type="button"
      variant="outline"
      onClick={() => setTheme(nextTheme)}
      className={sidebar ? "w-full justify-start" : undefined}
    >
      {theme === "dark" ? (
        <Sun data-icon="inline-start" className="size-4" />
      ) : (
        <Moon data-icon="inline-start" className="size-4" />
      )}
      {sidebar ? `Switch to ${nextTheme} mode` : `${theme === "dark" ? "Dark" : "Light"} mode`}
    </Button>
  );
}
EOF

write_file "$APP_ROOT/src/components/app/profile-menu.tsx" <<'EOF'
import * as React from "react";
import type { User } from "lemma-sdk";
import { Users } from "lucide-react";
import { useAuth, useCurrentUser } from "lemma-sdk/react";
import { LemmaUserMenu, type LemmaUserMenuItem } from "@/components/lemma/lemma-user-menu";
import { appConfig } from "@/app-config";
import { client } from "@/lib/client";

function hashPath(pathname: string) {
  if (pathname.startsWith("#")) return pathname;
  return pathname.startsWith("/") ? `#${pathname}` : `#/${pathname}`;
}

function displayNameForUser(user: User | null, fallbackEmail?: string) {
  const fullName = [user?.first_name, user?.last_name].filter(Boolean).join(" ").trim();
  return fullName || user?.email || fallbackEmail || "Account";
}

export function ProfileMenu({ sidebar = false }: { sidebar?: boolean }) {
  const auth = useAuth(client);
  const currentUser = useCurrentUser({
    client,
    enabled: auth.isAuthenticated,
  });
  const [isSigningOut, setIsSigningOut] = React.useState(false);

  const userName = displayNameForUser(currentUser.user, auth.user?.email);
  const userEmail = currentUser.user?.email || auth.user?.email || undefined;

  const menuItems = React.useMemo<LemmaUserMenuItem[]>(() => {
    if (!appConfig.membersPage) return [];
    return [
      {
        label: "Members",
        href: hashPath("/members"),
        icon: <Users className="size-4" />,
      },
    ];
  }, []);

  const handleSignOut = React.useCallback(async () => {
    if (isSigningOut) return;
    setIsSigningOut(true);
    try {
      await client.auth.signOut();
      window.location.reload();
    } finally {
      setIsSigningOut(false);
    }
  }, [isSigningOut]);

  return (
    <LemmaUserMenu
      userName={userName}
      userEmail={userEmail}
      isOnline={auth.isAuthenticated}
      menuItems={menuItems}
      onSignOut={() => {
        void handleSignOut();
      }}
      appearance="contained"
      density="comfortable"
      radius="lg"
      className={sidebar ? "w-full justify-between" : undefined}
    />
  );
}
EOF

if [[ "$MEMBERS_BLOCK_ENABLED" == "true" ]]; then
  write_file "$APP_ROOT/src/components/app/members-entry.tsx" <<'EOF'
import { useCurrentUser } from "lemma-sdk/react";
import { LemmaMembers } from "@/components/lemma/lemma-members";
import { client } from "@/lib/client";
import { hasPodId, runtimeConfig } from "@/lib/runtime-config";

export function MembersPage() {
  const currentUser = useCurrentUser({
    client,
    enabled: true,
  });

  return (
    <LemmaMembers
      client={client}
      podId={runtimeConfig.podId || undefined}
      enabled={hasPodId}
      currentUserId={currentUser.user?.id ?? null}
      title="Members"
      description="Manage the people who can access and operate this pod."
      allowRoleEdit
      allowRemove
      appearance="contained"
      density="comfortable"
      radius="lg"
      className="min-h-0"
    />
  );
}
EOF
else
  write_file "$APP_ROOT/src/components/app/members-entry.tsx" <<'EOF'
export function MembersPage() {
  return null;
}
EOF
fi

if [[ "$SEARCH_BLOCK_ENABLED" == "true" ]]; then
  write_file "$APP_ROOT/src/components/app/search-entry.tsx" <<'EOF'
import { Search } from "lucide-react";
import { LemmaGlobalSearch } from "@/components/lemma/lemma-global-search";
import { appConfig } from "@/app-config";
import { client } from "@/lib/client";
import { hasPodId, runtimeConfig } from "@/lib/runtime-config";
import { Button } from "@/components/lemma/ui/button";

function hashPath(pathname: string) {
  if (pathname.startsWith("#")) return pathname;
  return pathname.startsWith("/") ? `#${pathname}` : `#/${pathname}`;
}

function resolvePathTemplate(template: string, values: Record<string, unknown>) {
  return template.replace(/:([a-zA-Z0-9_]+)/g, (_, key) => {
    const value = values[key];
    if (value == null) return "";
    return encodeURIComponent(String(value));
  });
}

export function SearchControl() {
  if (!appConfig.search) return null;

  const assistantName =
    runtimeConfig.assistantName || appConfig.assistant?.assistantName || "";

  return (
    <div data-lemma-global-search-trigger>
      <LemmaGlobalSearch
        client={client}
        podId={runtimeConfig.podId || undefined}
        enabled={hasPodId}
        triggerLabel="Search"
        tables={appConfig.search.tables.map((table) => ({
          tableName: table.tableName,
          label: table.label,
          searchFields: table.searchFields,
          displayField: table.displayField || undefined,
          subtitleField: table.subtitleField || undefined,
          href: (record) =>
            hashPath(
              resolvePathTemplate(table.hrefTemplate, {
                tableName: table.tableName,
                ...record,
              }),
            ),
        }))}
        files={
          appConfig.search.files?.enabled
            ? {
                enabled: true,
                label: appConfig.search.files.label,
                href: (result) =>
                  hashPath(
                    resolvePathTemplate(appConfig.search?.files?.hrefTemplate ?? "/files?path=:path", {
                      path: result.path,
                    }),
                  ),
              }
            : undefined
        }
        assistant={
          assistantName
            ? {
                assistantName,
                label: "Assistant",
                href: appConfig.assistant?.pageEnabled
                  ? () => hashPath("/assistant")
                  : undefined,
              }
            : undefined
        }
      />
    </div>
  );
}

function openGlobalSearch() {
  document
    .querySelector<HTMLElement>("[data-lemma-global-search-trigger] button")
    ?.click();
}

export function SidebarSearchButton() {
  return (
    <Button type="button" variant="outline" className="w-full justify-start" onClick={openGlobalSearch}>
      <Search data-icon="inline-start" />
      Search
    </Button>
  );
}
EOF
else
  write_file "$APP_ROOT/src/components/app/search-entry.tsx" <<'EOF'
export function SearchControl() {
  return null;
}

export function SidebarSearchButton() {
  return null;
}
EOF
fi

if [[ "$ASSISTANT_BLOCK_ENABLED" == "true" ]]; then
  write_file "$APP_ROOT/src/components/app/assistant-entry.tsx" <<'EOF'
import { useAssistantController } from "lemma-sdk/react";
import { useSearchParams } from "react-router-dom";
import { AssistantExperienceView } from "@/components/lemma/assistant/assistant-experience";
import { appConfig } from "@/app-config";
import { client } from "@/lib/client";
import { hasPodId, runtimeConfig } from "@/lib/runtime-config";

function useDeskAssistantController() {
  return useAssistantController({
    client,
    podId: runtimeConfig.podId || undefined,
    assistantName:
      runtimeConfig.assistantName || appConfig.assistant?.assistantName || null,
    enabled:
      hasPodId &&
      Boolean(runtimeConfig.assistantName || appConfig.assistant?.assistantName),
  });
}

export function AssistantPage() {
  const controller = useDeskAssistantController();
  const [searchParams] = useSearchParams();
  const showConversationList = searchParams.get("assistant-nav") === "1";

  return (
    <section className="flex h-full min-h-0 flex-1 overflow-hidden">
      <AssistantExperienceView
        controller={controller}
        mode="page"
        showConversationList={showConversationList}
        className="h-full min-h-0 flex-1"
        appearance="default"
        density="comfortable"
        radius="lg"
        chromeStyle="subtle"
      />
    </section>
  );
}

export function AssistantRail() {
  const controller = useDeskAssistantController();

  return (
    <aside className="hidden min-h-0 min-w-0 flex-col overflow-hidden border-l border-border/70 bg-background/75 xl:flex">
      <AssistantExperienceView
        controller={controller}
        mode="side-panel"
        showConversationList={false}
        className="h-full min-h-0 flex-1"
        appearance="default"
        density="compact"
        radius="lg"
        chromeStyle="subtle"
      />
    </aside>
  );
}

export function AssistantPopup() {
  const controller = useDeskAssistantController();

  return (
    <AssistantExperienceView
      controller={controller}
      mode="popup"
      showConversationList={false}
      popupTriggerLabel="Open assistant"
      popupTriggerVariant="pill"
      appearance="default"
      density="comfortable"
      radius="lg"
      chromeStyle="subtle"
    />
  );
}
EOF
else
  write_file "$APP_ROOT/src/components/app/assistant-entry.tsx" <<'EOF'
export function AssistantPage() {
  return null;
}

export function AssistantRail() {
  return null;
}

export function AssistantPopup() {
  return null;
}
EOF
fi

ENV_EXAMPLE_CONTENT="$(cat <<EOF
VITE_LEMMA_POD_ID=$POD_ID
EOF
)"
if [[ "$ASSISTANT_BLOCK_ENABLED" == "true" ]]; then
  ENV_EXAMPLE_CONTENT="$ENV_EXAMPLE_CONTENT"$'\n'"VITE_LEMMA_ASSISTANT_NAME=$ASSISTANT_NAME"
fi
write_file "$APP_ROOT/.env.example" <<<"$ENV_EXAMPLE_CONTENT"
write_file "$APP_ROOT/.env.local" <<<"$ENV_EXAMPLE_CONTENT"

if [[ -f "$APP_ROOT/tsconfig.app.json" ]]; then
  write_file "$APP_ROOT/tsconfig.app.json" <<'EOF'
{
  "compilerOptions": {
    "tsBuildInfoFile": "./node_modules/.tmp/tsconfig.app.tsbuildinfo",
    "target": "ES2022",
    "useDefineForClassFields": true,
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "ignoreDeprecations": "6.0",
    "baseUrl": ".",
    "paths": {
      "@/*": ["./src/*"]
    },
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "moduleDetection": "force",
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": false,
    "noUnusedParameters": false,
    "noFallthroughCasesInSwitch": true
  },
  "include": ["src"]
}
EOF
else
  write_file "$APP_ROOT/tsconfig.json" <<'EOF'
{
  "compilerOptions": {
    "target": "ES2022",
    "useDefineForClassFields": true,
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "ignoreDeprecations": "6.0",
    "baseUrl": ".",
    "paths": {
      "@/*": ["./src/*"]
    },
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "moduleDetection": "force",
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": false,
    "noUnusedParameters": false,
    "noFallthroughCasesInSwitch": true
  },
  "include": ["src"]
}
EOF
fi

echo "Installing full Lemma registry component set..."
LEMMA_REGISTRY_ITEMS=(
  "lemma-ui"
  "lemma-action-surface"
  "lemma-activity-feed"
  "lemma-assistant-experience"
  "lemma-breadcrumbs"
  "lemma-comments"
  "lemma-detail-panel"
  "lemma-document-workspace"
  "lemma-file-browser"
  "lemma-global-search"
  "lemma-insights"
  "lemma-markdown-editor"
  "lemma-members"
  "lemma-notification-bell"
  "lemma-page-tree"
  "lemma-record-form"
  "lemma-records-view"
  "lemma-status-flow"
  "lemma-user-menu"
  "lemma-workflow-runner"
)

for registry_item in "${LEMMA_REGISTRY_ITEMS[@]}"; do
  install_registry_item "$APP_ROOT" "$registry_item"
done

echo "Lemma desk scaffold ready."
echo "Project: $APP_ROOT"
echo "Shell: $NAVIGATION"
if [[ "$SEARCH_BLOCK_ENABLED" == "true" ]]; then
  echo "Search: enabled"
else
  echo "Search: disabled"
fi
if [[ "$ASSISTANT_BLOCK_ENABLED" == "true" ]]; then
  echo "Assistant: $ASSISTANT_NAME ($ASSISTANT_MODE)"
else
  echo "Assistant: disabled"
fi
if [[ "$MEMBERS_BLOCK_ENABLED" == "true" ]]; then
  echo "Members: enabled"
else
  echo "Members: disabled"
fi
if [[ "$THEME_TOGGLE_ENABLED" == "true" ]]; then
  echo "Theme toggle: enabled"
else
  echo "Theme toggle: disabled"
fi
echo
echo "Next steps:"
echo "  cd $APP_ROOT"
echo "  npm run dev"
