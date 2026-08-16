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

# A capture gets its own tab, and gives it back.
#
# Every captured page used to land in the one shared tab and stay there. The
# session is deliberately long-lived -- one browser, one Xvfb display, one
# profile per sandbox -- so nothing ever reclaimed what a capture rendered, and
# Chrome keeps a process per site-instance. A workspace measured after a normal
# research session held 63 Chrome processes at 2123 MB RSS on a sandbox with
# 2048 MB total: `MemAvailable` was 14 MB, kswapd0 burned a third of the only
# vCPU, and every unrelated tool call in that sandbox degraded with it --
# `python -c pass` took over 12 seconds and `lemma --version` never returned at
# all. The agent saw `exit_code: 124` and no explanation.
#
# So the tab is closed on the way out, via trap: `set -e` means any capture
# step can abort the script, and the failing captures are exactly the expensive
# pages worth reclaiming. `--no-open` reuses whatever page the caller already
# has open -- that tab belongs to them, so this must not touch it.
CAPTURE_TAB=""
close_capture_tab() {
  if [[ -n "$CAPTURE_TAB" ]]; then
    agent-browser tab close "$CAPTURE_TAB" >/dev/null 2>&1 || true
    CAPTURE_TAB=""
  fi
}
trap close_capture_tab EXIT

if [[ "$OPEN_PAGE" == "1" ]]; then
  # Brings up the daemon, Xvfb and the dashboard if they are not up yet,
  # without navigating the caller's active tab.
  start-browser >/dev/null
  CAPTURE_TAB="lemma-capture-$$"
  agent-browser tab new --label "$CAPTURE_TAB" "$URL" >/dev/null
  # Bounded, because networkidle is a condition an ad-funded page never
  # reaches: something is always polling. Measured on the two news sites a user
  # actually asked for, the unbounded wait cost 32.6s and 40.0s per capture and
  # produced byte-for-byte the same markdown as a 5s cap did in 10.3s and
  # 32.7s. A tool call fetching two such pages renders them one at a time, so
  # that difference is most of the minute and a half the caller waits.
  #
  # The cap is not a page-load timeout: whatever has loaded by then is what
  # gets captured, and the fallback wait below still gives a slow page its
  # settle time. Pages that do go idle are unaffected -- they reach it in well
  # under the cap and this returns immediately.
  timeout "${NETWORKIDLE_TIMEOUT_S:-5}" \
    agent-browser wait --load networkidle >/dev/null 2>&1 \
    || agent-browser wait "$WAIT_MS" >/dev/null 2>&1 || true
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
