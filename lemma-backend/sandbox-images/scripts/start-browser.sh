#!/usr/bin/env bash
# `start-browser [url...]` -- the name `lemma-ensure-display` had before it was
# renamed, kept so that instructions written for it still work.
#
# Copies of the `browser` skill installed before the rename -- on a person's own
# machine, or in a pod -- tell an agent to run `start-browser` before anything
# else. Without this the first command such an agent runs is "command not
# found", and it tends to conclude the sandbox has no browser at all.
#
# Nothing needs a start step any more: `agent-browser open <url>` brings the
# display up itself (see `lemma-node-tool`). So this only does what the old
# command's callers expect -- bring the display up and, given a URL, open it.
set -euo pipefail

lemma-ensure-display
if [ "$#" -gt 0 ]; then
  exec agent-browser open "$@"
fi
