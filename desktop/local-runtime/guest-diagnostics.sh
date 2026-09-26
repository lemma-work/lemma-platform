set +e
echo '--- addresses ---'
ip -4 -o addr show 2>&1
echo '--- routes ---'
ip -4 route show 2>&1
echo '--- listening ---'
ss -ltn 2>&1 || cat /proc/net/tcp 2>&1
echo '--- data disk ---'
# Whether the disk takes discards (non-zero means it does): the one fact that
# says whether a trim can shrink data.raw on the Mac.
df -h /var/lib/lemma-data 2>&1
for queue in /sys/block/*/queue/discard_max_bytes; do
  [ -f "$queue" ] && echo "$queue $(cat "$queue" 2>&1)"
done
echo '--- containers ---'
/usr/local/bin/nerdctl ps -a 2>&1
for log in /var/log/lemma/*.log; do
  [ -f "$log" ] || continue
  echo "--- $log ---"
  tail -n 200 "$log" 2>&1
done
