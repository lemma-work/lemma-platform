#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: save-webpage <url> [options]

Save a rendered web page from the shared Agent Browser session.

Options:
  --formats <list>   Comma-separated: markdown,md,pdf,jpeg,jpg,png (default: markdown,pdf,jpeg)
  --out <dir>        Output directory (default: current working directory)
  --name <name>      Base output filename without extension
  --wait-ms <ms>     Fallback wait after navigation (default: 1000)
  --no-open          Reuse the current page instead of navigating first
  -h, --help         Show this help

Examples:
  save-webpage https://arxiv.org/abs/1706.03762
  save-webpage https://example.com --formats markdown,pdf,jpeg --out /workspace/research
EOF
}

slugify() {
  printf '%s' "$1" \
    | tr '[:upper:]' '[:lower:]' \
    | sed -E 's#^https?://##; s#[^a-z0-9._-]+#-#g; s#^-+##; s#-+$##; s#-{2,}#-#g' \
    | cut -c1-120
}

URL=""
FORMATS="markdown,pdf,jpeg"
OUT_DIR="."
NAME=""
WAIT_MS="1000"
OPEN_PAGE="1"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --formats)
      FORMATS="${2:-}"
      shift 2
      ;;
    --out)
      OUT_DIR="${2:-}"
      shift 2
      ;;
    --name)
      NAME="${2:-}"
      shift 2
      ;;
    --wait-ms)
      WAIT_MS="${2:-}"
      shift 2
      ;;
    --no-open)
      OPEN_PAGE="0"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    -*)
      echo "Unknown option: $1" >&2
      usage
      exit 2
      ;;
    *)
      if [[ -z "$URL" ]]; then
        URL="$1"
        shift
      else
        echo "Unexpected argument: $1" >&2
        usage
        exit 2
      fi
      ;;
  esac
done

if [[ -z "$URL" && "$OPEN_PAGE" == "1" ]]; then
  usage
  exit 2
fi

mkdir -p "$OUT_DIR"

if [[ "$OPEN_PAGE" == "1" ]]; then
  start-browser "$URL" >/dev/null
  agent-browser wait --load networkidle >/dev/null 2>&1 || agent-browser wait "$WAIT_MS" >/dev/null 2>&1 || true
fi

PAGE_URL="$(agent-browser get url)"
PAGE_TITLE="$(agent-browser get title)"
if [[ -z "$NAME" ]]; then
  NAME="$(slugify "${PAGE_TITLE:-$PAGE_URL}")"
fi
if [[ -z "$NAME" ]]; then
  NAME="page"
fi

IFS=',' read -r -a FORMAT_LIST <<< "$FORMATS"
for raw_format in "${FORMAT_LIST[@]}"; do
  format="$(printf '%s' "$raw_format" | tr '[:upper:]' '[:lower:]' | xargs)"
  case "$format" in
    markdown|md)
      html_file="$(mktemp)"
      agent-browser --max-output 50000000 get html html > "$html_file"
      node /usr/local/lib/webpage-to-markdown.mjs "$html_file" \
        --url "$PAGE_URL" \
        --title "$PAGE_TITLE" \
        > "$OUT_DIR/$NAME.md"
      rm -f "$html_file"
      printf 'markdown %s\n' "$OUT_DIR/$NAME.md"
      ;;
    pdf)
      agent-browser pdf "$OUT_DIR/$NAME.pdf" >/dev/null
      printf 'pdf %s\n' "$OUT_DIR/$NAME.pdf"
      ;;
    jpeg|jpg)
      agent-browser screenshot --full --screenshot-format jpeg --screenshot-quality 85 "$OUT_DIR/$NAME.jpg" >/dev/null
      printf 'jpeg %s\n' "$OUT_DIR/$NAME.jpg"
      ;;
    png)
      agent-browser screenshot --full "$OUT_DIR/$NAME.png" >/dev/null
      printf 'png %s\n' "$OUT_DIR/$NAME.png"
      ;;
    "")
      ;;
    *)
      echo "Unsupported format: $raw_format" >&2
      exit 2
      ;;
  esac
done
