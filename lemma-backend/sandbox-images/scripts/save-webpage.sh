#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: save-webpage <url> [options]

Save a rendered web page from the shared Agent Browser session.

Options:
  --formats <list>   Comma-separated: markdown,md,pdf,jpeg,jpg,png (default: markdown,pdf,jpeg)
  --full-page        Capture the whole scroll, not just the viewport. Slow and
                     large -- one news page measured 12.4s and 7.0 MB.
  --screenshot-quality <0-100>  JPEG quality (default 70).
  --out <dir>        Output directory (default: current working directory)
  --name <name>      Base output filename without extension
  --wait-ms <ms>     Fallback wait after navigation (default: 1000)
  --no-open          Reuse the current page instead of navigating first
  -h, --help         Show this help

Examples:
  save-webpage https://arxiv.org/abs/1706.03762
  save-webpage https://example.com --formats markdown,pdf,jpeg --out ~/research
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
# The viewport, not the whole page, and not at maximum quality.
#
# `--full` at quality 85 produced a 7.0 MB jpeg in 12.4s for one news page,
# measured. That is a default nobody asked for: it is slow, it is most of a
# tool call's budget, and it lands a file that size in somebody's workspace
# for a screenshot they wanted in order to *look at the page*. A viewport
# shot answers that. `--full-page` and `--screenshot-quality` are there for
# the times the whole scroll or the detail genuinely matters.
FULL_PAGE=""
SCREENSHOT_QUALITY="70"

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
    --full-page)
      FULL_PAGE="--full"
      shift
      continue
      ;;
    --screenshot-quality)
      SCREENSHOT_QUALITY="${2:-70}"
      shift 2
      continue
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
# Absolute from here on, and this is load-bearing rather than tidy.
#
# `agent-browser` resolves a relative path in the *daemon*, not in this
# process -- and the daemon's working directory is whichever one happened to
# start the browser. One browser serves every conversation in a sandbox, so
# running this from conversation B wrote the markdown here (the shell does
# that) and the jpeg into conversation A's directory. Measured exactly that.
OUT_DIR="$(cd "$OUT_DIR" && pwd)"

# A capture gets its own tab, and gives it back.
#
# Every captured page used to land in the one shared tab and stay there. The
# session is deliberately long-lived -- one browser, one Xvfb display, one
# profile per sandbox -- so nothing ever reclaimed what a capture rendered, and
# Chrome keeps a process per site-instance. A workspace measured after a normal
# research session held 63 Chrome processes (2123 MB summed across them, which
# over-counts: see below) RSS on a sandbox with
# 2048 MB total: `MemAvailable` was 14 MB, kswapd0 burned a third of the only
# vCPU, and every unrelated tool call in that sandbox degraded with it --
# `python -c pass` took over 12 seconds and `lemma --version` never returned at
# all. The agent saw `exit_code: 124` and no explanation.
#
# A correction to the arithmetic above, since it has been quoted as a reason
# not to open a second tab: summing per-process RSS counts Chrome's shared
# mappings once per process. Measured against the cgroup, which is what
# actually OOMs, five concurrent tabs in one browser peaked at 1656 MiB of
# 2048 and fell back to baseline when they closed. Tabs are cheap. What is
# not cheap is a second *browser*: three captures with a session each
# measured 75.4s against 11.0s for the same three run serially through one
# warm browser, because the sandbox has one vCPU.
#
# So the tab is closed on the way out, via trap: `set -e` means any capture
# step can abort the script, and the failing captures are exactly the expensive
# pages worth reclaiming. `--no-open` reuses whatever page the caller already
# has open -- that tab belongs to them, so this must not touch it.
# Is a browser already serving this profile? The port file alone is not
# enough -- Chrome leaves it behind -- so the port is probed too.
browser_is_live() {
  local port_file="${AGENT_BROWSER_PROFILE:-/home/user/.lemma/browser/profile}/DevToolsActivePort"
  [[ -r "$port_file" ]] || return 1
  local port
  port="$(head -1 "$port_file" 2>/dev/null)" || return 1
  [[ -n "$port" ]] || return 1
  curl -fsS -m 2 -o /dev/null "http://127.0.0.1:${port}/json/version" 2>/dev/null
}

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
  #
  # Probed first, because the full ensure costs 1.5-1.8s on a 1 vCPU sandbox
  # and nearly all of it is `agent-browser open` re-confirming a browser that
  # is already up. A capture in a warm sandbox is the common case and it was
  # paying that every time -- about a third of a simple capture. The probe is
  # the same question `live_port` asks: a recorded port, and something
  # answering on it.
  if ! browser_is_live; then
    lemma-ensure-display >/dev/null
  fi
  CAPTURE_TAB="lemma-capture-$$"
  agent-browser tab new --label "$CAPTURE_TAB" "$URL" >/dev/null
  # Wait for the *text to stop changing*, not for the network to go quiet.
  #
  # networkidle is the wrong question twice over. An ad-funded page never
  # reaches it -- something is always polling -- and a React page reaches
  # `domcontentloaded` with an empty shell, so neither signal says "there is
  # something here to read". This used to wrap networkidle in `timeout 5`,
  # which made it worse rather than bounded: killing the CLI leaves the wait
  # pending in the daemon and the next CDP call queues behind it. Measured on
  # reuters, the call after the cap blocked 20.3s whatever it was --
  # `window.stop()` blocked exactly as long as `get url`. The cap moved the
  # wait; it never shortened it.
  #
  # So: poll the rendered text length until it holds still, inside a single
  # `eval` that bounds itself. One CDP call, always resolves, nothing left
  # pending. Measured, wall clock and extracted markdown:
  #
  #                      timeout-5 networkidle    this
  #   reuters            27.0s  2560 chars        3.7s  2560 chars
  #   news.ycombinator    1.8s 15669              0.3s 15669
  #   wikipedia           2.4s 187571             3.6s 187571
  #   example.com         --                      0.2s   232
  #
  # Three stable samples rather than two: at two, reuters returned 200 chars
  # short. The deadline is what protects a page that never settles.
  agent-browser wait --load domcontentloaded >/dev/null 2>&1 || true
  agent-browser eval --stdin <<'SETTLE' >/dev/null 2>&1 || \
    agent-browser wait "$WAIT_MS" >/dev/null 2>&1 || true
(async () => {
  const deadline = Date.now() + 6000;
  const size = () => (document.body ? document.body.innerText.length : 0);
  let last = -1, stable = 0;
  while (Date.now() < deadline) {
    await new Promise(r => setTimeout(r, 250));
    const now = size();
    if (now === last && now > 0) { if (++stable >= 3) break; } else { stable = 0; }
    last = now;
  }
  return last;
})()
SETTLE
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
      # shellcheck disable=SC2086 # $FULL_PAGE is a flag or empty, on purpose.
      agent-browser screenshot $FULL_PAGE --screenshot-format jpeg \
        --screenshot-quality "$SCREENSHOT_QUALITY" "$OUT_DIR/$NAME.jpg" >/dev/null
      printf 'jpeg %s\n' "$OUT_DIR/$NAME.jpg"
      ;;
    png)
      # shellcheck disable=SC2086
      agent-browser screenshot $FULL_PAGE "$OUT_DIR/$NAME.png" >/dev/null
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
