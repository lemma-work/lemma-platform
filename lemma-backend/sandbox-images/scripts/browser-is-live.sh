#!/usr/bin/env bash
# Is a browser already serving this profile? Exit 0 if yes.
#
# The port file alone is not enough -- Chrome leaves it behind when it dies,
# and `agent-browser` leaves one behind that names the *previous* launch -- so
# the port is probed as well. Answering this without starting anything is the
# whole point: `agent-browser get cdp-url` would report the port and launch a
# browser to do it, which costs a few hundred megabytes in a 2 GB sandbox.
#
# Shared rather than copied because two callers need exactly this question and
# they must not drift: `save-webpage` asks it to skip a 1.5-1.8s `ensure` on a
# warm sandbox, and `lemma-ensure-display` asks it to decide whether it may
# open a page at all -- and opening one over a browser somebody is watching
# is the bug that made it worth extracting.
set -euo pipefail

port_file="${AGENT_BROWSER_PROFILE:-/home/user/.lemma/browser/profile}/DevToolsActivePort"
[ -r "$port_file" ] || exit 1
port="$(head -1 "$port_file" 2>/dev/null)" || exit 1
[ -n "$port" ] || exit 1
exec curl -fsS -m 2 -o /dev/null "http://127.0.0.1:${port}/json/version"
