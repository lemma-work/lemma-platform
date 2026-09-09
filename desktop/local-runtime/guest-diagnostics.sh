set +e
echo '--- addresses ---'
ip -4 -o addr show 2>&1
echo '--- routes ---'
ip -4 route show 2>&1
echo '--- listening ---'
ss -ltn 2>&1 || cat /proc/net/tcp 2>&1
echo '--- containers ---'
/usr/local/bin/nerdctl ps -a 2>&1
for log in /var/log/lemma/*.log; do
  [ -f "$log" ] || continue
  echo "--- $log ---"
  tail -n 200 "$log" 2>&1
done
