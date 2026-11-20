#!/usr/bin/env bash
# Integration check for lemma-streamer, against a real workspace image.
#
#   lemma-backend/sandbox-images/streamer/integration/run.sh [workspace-image]
#
# Builds the streamer, starts a sandbox container (Xvfb + Chrome on a
# scrolling page) at 1 vCPU, and checks with `streamcheck`:
#   view ticket -> frames arrive, the input channel is refused
#   control ticket -> input reaches X
#   a second control ticket revokes the first
#   forged / expired / other-machine tickets are refused
#   relay-only through a coturn container (UDP and TCP)
# then measures the streamer's CPU and bitrate on the scrolling page.
# Every container and network it makes is removed on exit.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
STREAMER="$(dirname "$HERE")"
BASE="${1:-$(docker images --format '{{.Repository}}:{{.Tag}}' | grep '^lemma-workspace:' | head -1)}"
TAG="lemma-streamer-it:local"
RUN_ID="lst-$$"
KEY="$(openssl rand -hex 32)"
MACHINE="it-machine"
TURN_SECRET="$(openssl rand -hex 16)"
CPUS="${CPUS:-1}"

CTX="$(mktemp -d)"
# shellcheck disable=SC2329  # invoked by the trap below
cleanup() {
  docker rm -f "$RUN_ID-sbx" "$RUN_ID-turn" "$RUN_ID-viewer" >/dev/null 2>&1 || true
  docker network rm "$RUN_ID-a" "$RUN_ID-b" >/dev/null 2>&1 || true
  rm -rf "$CTX"
}
trap cleanup EXIT INT TERM

fail=0
check() { # name, condition-result (0 ok)
  if [ "$2" = 0 ]; then echo "PASS $1"; else echo "FAIL $1"; fail=1; fi
}
jq_get() { python3 -c 'import json,sys; d=json.loads(sys.stdin.read().strip().splitlines()[-1]); print(d.get(sys.argv[1]))' "$1"; }

echo "base image: $BASE"
docker build -q --target out -o "type=local,dest=$CTX/bin" "$STREAMER" >/dev/null
cp "$HERE"/{Dockerfile,probe.html,sandbox.sh,mint.py} "$CTX/"
cp "$STREAMER/../scripts/start-streamer.sh" "$CTX/"
docker build -q --build-arg BASE="$BASE" -t "$TAG" "$CTX" >/dev/null

docker network create "$RUN_ID-a" >/dev/null
docker network create "$RUN_ID-b" >/dev/null
docker run -d --name "$RUN_ID-sbx" --network "$RUN_ID-a" --cpus "$CPUS" --shm-size 512m \
  -e STREAM_KEY_HEX="$KEY" -e MACHINE_ID="$MACHINE" "$TAG" >/dev/null
docker network connect "$RUN_ID-b" "$RUN_ID-sbx"
sleep 3
sbx() { docker exec "$RUN_ID-sbx" "$@"; }
mint() { sbx python3 /it/mint.py "$KEY" "${2:-$MACHINE}" "$1" "${3:-60}"; }

sbx start-streamer
check "start-streamer is idempotent" "$(sbx start-streamer >/dev/null 2>&1; echo $?)"
SIG=ws://127.0.0.1:4860/signal

# 1. view: frames, and no input path.
out="$(sbx streamcheck -signal $SIG -ticket "$(mint view)" -policy all -seconds 2 -try-input || true)"
echo "  view: $out"
frames="$(echo "$out" | jq_get frames)"
check "view ticket gets >= 20 frames in 2 s" "$([ "$frames" -ge 20 ] && echo 0 || echo 1)"
check "view ticket's input channel is refused" "$([ "$(echo "$out" | jq_get input_channel)" = refused ] && echo 0 || echo 1)"
sbx grep -q 'refusing data channel "input"' /tmp/lemma-streamer.log; check "streamer logged the refusal" $?

# 1b. a viewer that offers only VP8 gets VP8.
out="$(sbx streamcheck -signal $SIG -ticket "$(mint view)" -policy all -seconds 2 -codec vp8 || true)"
echo "  vp8: $out"
check "VP8-only viewer gets VP8 frames" "$([ "$(echo "$out" | jq_get codec)" = vp8 ] && [ "$(echo "$out" | jq_get frames)" -ge 20 ] && echo 0 || echo 1)"

# 2. control: input reaches X.
sbx xdotool mousemove 5 5
out="$(sbx streamcheck -signal $SIG -ticket "$(mint control)" -policy all -seconds 2 -try-input -move 640,400 || true)"
echo "  control: $out"
check "control ticket's input channel opens" "$([ "$(echo "$out" | jq_get input_channel)" = open ] && echo 0 || echo 1)"
loc="$(sbx xdotool getmouselocation)"
check "pointer moved to 640,400 ($loc)" "$(echo "$loc" | grep -q 'x:640 y:400' && echo 0 || echo 1)"

# 3. a second controller revokes the first.
sbx sh -c "streamcheck -signal $SIG -ticket '$(mint control)' -policy all -seconds 6 -try-input > /tmp/first.json 2>&1 &"
sleep 3
sbx streamcheck -signal $SIG -ticket "$(mint control)" -policy all -seconds 2 -try-input >/dev/null || true
sleep 3
first="$(sbx cat /tmp/first.json)"
echo "  first controller: $first"
check "a newer control ticket revokes the first controller" "$([ "$(echo "$first" | jq_get revoked)" = True ] && echo 0 || echo 1)"

# 3b. view upgraded with a control ticket.
out="$(sbx streamcheck -signal $SIG -ticket "$(mint view)" -control-ticket "$(mint control)" -take -policy all -seconds 3 -try-input || true)"
echo "  take: $out"
check "view -> control upgrade" "$([ "$(echo "$out" | jq_get mode_after_take)" = control ] && [ "$(echo "$out" | jq_get input_channel)" = open ] && echo 0 || echo 1)"

# 4. tickets that must not work.
forged="$(mint control | sed 's/.$/A/')"
for name in forged expired other-machine; do
  case $name in
    forged) t="$forged" ;;
    expired) t="$(mint view "$MACHINE" -30)" ;;
    other-machine) t="$(mint view someone-else)" ;;
  esac
  out="$(sbx streamcheck -signal $SIG -ticket "$t" -policy all -seconds 1 || true)"
  check "$name ticket refused ($(echo "$out" | jq_get error))" "$(echo "$out" | grep -q '"connected":false' && echo "$out" | grep -q 'refused' && echo 0 || echo 1)"
done

# 5. relay-only through coturn, viewer on another network.
# Both networks before it starts: coturn listens on the interfaces it finds
# at start-up and never on one added later.
docker create --name "$RUN_ID-turn" --network "$RUN_ID-a" coturn/coturn:4.7.0 \
  -n --log-file=stdout --listening-port=3478 --realm=lemma.test --use-auth-secret \
  --static-auth-secret="$TURN_SECRET" --no-tls --no-dtls --fingerprint >/dev/null
docker network connect "$RUN_ID-b" "$RUN_ID-turn"
docker start "$RUN_ID-turn" >/dev/null
sleep 1
creds() {
  python3 - "$TURN_SECRET" <<'PY'
import base64, hashlib, hmac, sys, time
u = f"{int(time.time()) + 600}:it"
p = base64.b64encode(hmac.new(sys.argv[1].encode(), u.encode(), hashlib.sha1).digest()).decode()
print(u, p)
PY
}
read -r TU TP < <(creds)
for proto in udp tcp; do
  ICE="[{\"urls\":[\"turn:$RUN_ID-turn:3478?transport=$proto\"],\"username\":\"$TU\",\"credential\":\"$TP\"}]"
  out="$(docker run --rm --name "$RUN_ID-viewer" --network "$RUN_ID-b" --entrypoint streamcheck "$TAG" \
    -signal "ws://$RUN_ID-sbx:4861/signal" -ticket "$(mint view)" -ice "$ICE" -policy relay -relay-only -seconds 3 || true)"
  echo "  turn/$proto: $out"
  check "relay-only over TURN/$proto connects and plays" \
    "$(echo "$out" | grep -q '"connected":true' && [ "$(echo "$out" | jq_get frames)" -ge 30 ] && echo "$out" | grep -q '"selected_pair":"relay' && echo 0 || echo 1)"
done

# 6. cost on the scrolling page.
pid="$(sbx pgrep -x lemma-streamer)"
sbx sh -c "streamcheck -signal $SIG -ticket '$(mint view)' -policy all -seconds 14 > /tmp/cost.json 2>&1 &"
sleep 3
# shellcheck disable=SC2016  # awk's own fields, not the shell's
t0="$(sbx awk '{print $14+$15}' "/proc/$pid/stat")"; sleep 10
# shellcheck disable=SC2016
t1="$(sbx awk '{print $14+$15}' "/proc/$pid/stat")"
sleep 2
cost="$(sbx cat /tmp/cost.json)"
cores="$(python3 -c "print(round(($t1-$t0)/100/10, 3))")"
mbps="$(echo "$cost" | python3 -c 'import json,sys; d=json.loads(sys.stdin.read()); print(round(d["bytes"]*8/14/1e6, 2), round(d["frames"]/14, 1))')"
echo "COST cpus=$CPUS streamer_cores=$cores mbps_fps=$mbps health=$(sbx curl -s 127.0.0.1:4860/health)"

exit $fail
