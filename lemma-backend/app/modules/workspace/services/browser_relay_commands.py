"""What the backend asks a sandbox's shell to do before a viewer arrives.

Its own module because it is a different kind of thing from the rest of the
relay client: that file is about talking to a process over HTTP, and this is a
shell script the backend ships in a `start_process` call. Most of what is here
is rollout scaffolding -- `command -v` fallbacks and an exported variable that
exist only to reach sandboxes running an image older than this code -- and each
piece carries the date it can be deleted in its own comment. Kept together so
that when the image has rolled everywhere, the deletion is one file.
"""

from __future__ import annotations

from app.modules.workspace.services.browser_proxy import (
    BROWSER_PROXY_DECISION_PATH,
)

#: Bring the display stack up, on this image or on the one before it.
#:
#: `lemma-ensure-display` is `start-browser` renamed. The rename ships in the
#: image and the image rollout is deliberately deferred, so for as long as
#: that gap is open a backend that only knew the new name would fail to start
#: the browser at all on every sandbox still running the old one -- not
#: degrade, fail: the command does not exist, the relay never comes up, and
#: the viewer gets "the browser relay did not start".
#:
#: Falling back costs one `command -v`. It comes out when the images are
#: rolled, and until then this is the difference between a deploy that is
#: safe in either order and one that is not.
#:
#: `start-vnc-bridge` is the viewing half -- x11vnc and websockify, 66 MiB
#: measured -- which `lemma-ensure-display` no longer starts, because an
#: agent doing research pays for it and nobody is watching. This is the
#: viewer's own path, so this is where it is asked for. Guarded by
#: `command -v` for the same rollout reason as the line above: on an image
#: that predates the split, the two are already running and there is
#: nothing to start.
#: The proxy decision, applied here as well as in the script, and this is
#: the half that reaches the fleet that exists today.
#:
#: The script reads the decision file itself -- but the script lives in the
#: *image*, and the profile digest is deliberately not bumped in this branch,
#: because bumping it refuses reuse of every existing sandbox and on E2B that
#: means a new disk and a person's files gone. So on every sandbox already
#: running, `lemma-ensure-display` is still the old one, which knows only
#: `AGENT_BROWSER_PROXY`. Without these lines the server could not withdraw a
#: proxy from a single sandbox currently proxied -- which is the entire
#: feature, aimed exactly at the fleet that cannot get the new script.
#:
#: Harmless on a new image, and deliberately so: that script begins by
#: `unset`ting `AGENT_BROWSER_PROXY` and reading the file itself, so the
#: export below is overwritten by the same answer it came from. The two
#: cannot disagree, because both read one file.
#:
#: Deletable when the image has rolled everywhere, like the `command -v`
#: fallbacks around it.
APPLY_PROXY_DECISION = (
    f'if [ -r "{BROWSER_PROXY_DECISION_PATH}" ]; then '
    # `|| true`, never `|| VALUE=`: `read` returns non-zero at end-of-file
    # without a trailing newline and has already assigned the line by then,
    # and the server writes the URL unterminated. Clearing it there is the
    # bug that made the whole mechanism inert in the script.
    f'  IFS= read -r LEMMA_PROXY < "{BROWSER_PROXY_DECISION_PATH}" || true; '
    '  if [ -n "${LEMMA_PROXY:-}" ]; then '
    '    export AGENT_BROWSER_PROXY="$LEMMA_PROXY"; '
    "  else "
    # An empty decision is the server saying "no proxy", which has to be able
    # to undo a value baked into an older sandbox's environment at create.
    "    unset AGENT_BROWSER_PROXY; "
    "  fi; "
    "  unset LEMMA_PROXY; "
    "fi; "
)

ENSURE_DISPLAY = (
    APPLY_PROXY_DECISION + "if command -v lemma-ensure-display >/dev/null 2>&1; then "
    "  lemma-ensure-display; "
    "else "
    "  start-browser; "
    "fi; "
    "if command -v start-vnc-bridge >/dev/null 2>&1; then "
    "  start-vnc-bridge; "
    "fi"
)


__all__ = ["ENSURE_DISPLAY"]
