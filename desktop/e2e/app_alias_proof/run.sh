#!/bin/bash
# Prove, in the engine the macOS app ships, that a pod app framed through its
# locald alias is signed in -- and that the same app framed on its own address
# is not. See README.md. macOS only; needs Xcode's swift and a Rust toolchain.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
desktop="$(cd "$here/../.." && pwd)"
work="$(mktemp -d)"
cleanup() {
    # Closing their stdin is how both are told to stop.
    exec 3>&- 4>&- 2>/dev/null || true
    for pid in ${alias_pid:-} ${stand_in_pid:-}; do
        kill "$pid" 2>/dev/null || true
        wait "$pid" 2>/dev/null || true
    done
    rm -rf "$work"
}
trap cleanup EXIT

free_port() { python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])'; }
workspace_port="$(free_port)"
backend_port="$(free_port)"

(cd "$desktop" && cargo build -q -p lemma-locald --example app_alias_serve --locked)
swiftc -O -o "$work/probe" "$here/probe.swift" -framework AppKit -framework WebKit

mkfifo "$work/stand_in.in" "$work/alias.in"
python3 "$here/stand_in.py" "$workspace_port" "$backend_port" <"$work/stand_in.in" >"$work/stand_in.out" &
stand_in_pid=$!
exec 3>"$work/stand_in.in"
until grep -q ready "$work/stand_in.out" 2>/dev/null; do sleep 0.1; done

canonical="http://proof.apps.lemma.localhost:$backend_port/"
"$desktop/target/debug/examples/app_alias_serve" "$work/state" "$workspace_port" "$backend_port" "$canonical" \
    <"$work/alias.in" >"$work/alias.out" &
alias_pid=$!
exec 4>"$work/alias.in"
until [[ -s "$work/alias.out" ]]; do sleep 0.1; done
alias_url="$(head -1 "$work/alias.out")"

api="http://app.lemma.localhost:$backend_port"
encode() { python3 -c 'import sys,urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$1"; }
workspace="http://app.lemma.localhost:$workspace_port/?api=$(encode "$api")"

echo "workspace: http://app.lemma.localhost:$workspace_port"
echo "app:       $canonical"
echo "alias:     $alias_url"
"$work/probe" "$workspace&frame=$(encode "$alias_url")" "$canonical" >"$work/aliased.json"
"$work/probe" "$workspace&frame=$(encode "$canonical")" "$canonical" >"$work/canonical.json"

python3 - "$work/aliased.json" "$work/canonical.json" "$alias_url" <<'PY'
import json, sys
aliased = json.load(open(sys.argv[1]))
canonical = json.load(open(sys.argv[2]))
alias_origin = sys.argv[3].rstrip("/")
print("framed through the alias: ", json.dumps(aliased, sort_keys=True))
print("framed on its own address:", json.dumps(canonical, sort_keys=True))
checks = {
    "the workspace on app.lemma.localhost is a secure context": aliased["workspace"]["secure"] is True,
    "the workspace has navigator.mediaDevices.getUserMedia": aliased["workspace"]["mediaDevices"] is True,
    "the workspace has crypto.subtle": aliased["workspace"]["subtle"] is True,
    "sign-in succeeded": aliased["workspace"]["signIn"] == 200,
    "the aliased frame is signed in (cookie sent to /_lemma)":
        aliased["workspace"]["frame"] != "timeout"
        and aliased["workspace"]["frame"]["data"]["status"] == 200
        and aliased["workspace"]["frame"]["data"]["email"] == "owner@example.com",
    "the aliased frame runs on the alias origin":
        aliased["workspace"]["frame"] != "timeout"
        and aliased["workspace"]["frame"]["origin"] == alias_origin,
    "the canonical URL top-level is signed in": aliased["topLevelApp"]["status"] == 200,
    "control: the canonical URL framed is NOT signed in (why the alias exists)":
        canonical["workspace"]["frame"] != "timeout"
        and canonical["workspace"]["frame"]["data"]["status"] == 401,
}
failed = [name for name, ok in checks.items() if not ok]
for name, ok in checks.items():
    print(("PASS " if ok else "FAIL ") + name)
sys.exit(1 if failed else 0)
PY
