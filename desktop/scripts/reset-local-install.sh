#!/usr/bin/env bash
# Return this Mac to the state of a machine that has never run Lemma Desktop.
#
# A first-launch bug is only reproducible on a first launch, and the state a
# previous install leaves behind -- a daemon still running, a VM whose disks
# have moved, a half-written runtime -- is exactly what makes the second attempt
# look different from the first. This deletes all of it.
#
# It does NOT uninstall the app. Delete /Applications/Lemma.app yourself if that
# is what you want; leaving it means you can re-test immediately.
set -euo pipefail

support="${HOME}/Library/Application Support/Lemma"
caches="${HOME}/Library/Caches/work.lemma.desktop"
webkit="${HOME}/Library/WebKit/work.lemma.desktop"
prefs="${HOME}/Library/Preferences/work.lemma.desktop.plist"
saved="${HOME}/Library/Saved Application State/work.lemma.desktop.savedState"

say() { printf '  %s\n' "$1"; }

if [[ "${1:-}" != "--yes" ]]; then
  printf '\nThis deletes every trace of local Lemma state:\n\n'
  for path in "${support}" "${caches}" "${webkit}" "${prefs}" "${saved}"; do
    [[ -e "${path}" ]] && say "${path}"
  done
  printf '\nWorkspaces, databases and credentials in them are destroyed.\n'
  printf 'Re-run with --yes to proceed.\n\n'
  exit 1
fi

printf '\n→ Stopping anything still running\n'
osascript -e 'tell application "Lemma" to quit' >/dev/null 2>&1 || true
# The daemon deliberately outlives the app, and the VM outlives the daemon, so
# quitting is not enough on its own.
for name in lemma-locald lemma-vz lemma-agent-host lemma-runtime; do
  if pkill -x "${name}" 2>/dev/null; then say "stopped ${name}"; fi
done
sleep 2
for name in lemma-locald lemma-vz lemma-agent-host lemma-runtime; do
  if pkill -9 -x "${name}" 2>/dev/null; then say "killed ${name}"; fi
done

printf '\n→ Removing local state\n'
for path in "${support}" "${caches}" "${webkit}" "${prefs}" "${saved}"; do
  if [[ -e "${path}" ]]; then
    rm -rf "${path}"
    say "removed ${path}"
  fi
done

# locald keeps operator secrets in the login keychain, keyed to its identifier.
# They are meaningless once the databases they unlock are gone, and leaving them
# means the next install adopts credentials for a workspace that no longer exists.
printf '\n→ Removing keychain entries\n'
removed=0
while security delete-generic-password -s "work.lemma.locald" >/dev/null 2>&1; do
  removed=$((removed + 1))
done
say "removed ${removed} keychain item(s)"

printf '\n✓ This Mac now looks like it has never run Lemma.\n'
if [[ -d /Applications/Lemma.app ]]; then
  printf '  /Applications/Lemma.app is still installed — open it for a clean first run.\n\n'
else
  printf '  Install a DMG and open it for a clean first run.\n\n'
fi
