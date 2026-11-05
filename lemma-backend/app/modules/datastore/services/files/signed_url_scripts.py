"""The two Redis scripts behind a public link's budget (``signed_url``).

Each runs server-side so its check and its write are one atomic step. Kept
apart from the store that calls them because they are a contract with what
Redis holds — the hash's fields — more than they are Python.
"""

# Atomically charge ARGV[1] open-units against the link's budget and ARGV[2]
# bytes against its embedded allowance, and return its claims, as one step.
# ARGV[3] is 1 when the request is for something the page embeds, and ARGV[4]
# the grace, in milliseconds, those requests keep after the page's last open.
#
# {-1}: missing, expired, or cached before links went live (no `budget_units`;
# dropped so the record rebuilds it). {-2, in_grace}: the budget is spent or
# this request would not fit in what is left — the whole request, which is what
# makes the budget a ceiling rather than a line one response may cross.
# {-3}: only the embedded allowance would be crossed. Otherwise a fixed-arity
# {exhausted, pod_id, path, created_by_user_id, minted_by_workload,
# content_type, filename}.
# Fixed arity matters: Redis truncates a returned Lua table at its first nil, so
# a hash missing one field used to hand Python a short list and raise IndexError
# — a 500 where the honest answer was 404. Every field is coerced here instead.
#
# The grace exists because a browser asks in order: the page, then its
# pictures. The open that spends a link's last allowance is still a reader
# looking at the page, so what it embeds keeps loading for `grace` after it —
# `loading="lazy"` pictures arrive as they scroll — bounded, as ever, by the
# embedded allowance. The key is shortened to the grace when the budget is
# spent, so it cleans itself up. The clock is Redis's own, so every worker reads
# the same one.
CONSUME_LUA = """
if redis.call('EXISTS', KEYS[1]) == 0 then
  return {-1}
end
if redis.call('HEXISTS', KEYS[1], 'budget_units') == 0 then
  redis.call('DEL', KEYS[1])
  return {-1}
end
local wanted = tonumber(ARGV[1]) or 0
local for_embedded = ARGV[3] == '1'
local grace = tonumber(ARGV[4]) or 0
local clock = redis.call('TIME')
local now = tonumber(clock[1]) * 1000 + math.floor(tonumber(clock[2]) / 1000)
local budget = tonumber(redis.call('HGET', KEYS[1], 'budget_units')) or 0
local spent = tonumber(redis.call('HGET', KEYS[1], 'spent_units')) or 0
if budget > 0 and (spent >= budget or spent + wanted > budget) then
  local at = tonumber(redis.call('HGET', KEYS[1], 'exhausted_at'))
  local in_grace = at ~= nil and now < at + grace
  if not (for_embedded and wanted == 0 and in_grace) then
    if in_grace then
      return {-2, 1}
    end
    return {-2, 0}
  end
end
local embedded = tonumber(ARGV[2]) or 0
if embedded > 0 then
  local allowance = tonumber(redis.call('HGET', KEYS[1], 'embedded_budget_bytes')) or 0
  local used = tonumber(redis.call('HGET', KEYS[1], 'embedded_spent_bytes')) or 0
  if used + embedded > allowance then
    return {-3}
  end
  redis.call('HINCRBY', KEYS[1], 'embedded_spent_bytes', embedded)
end
local exhausted = 0
if wanted > 0 then
  spent = redis.call('HINCRBY', KEYS[1], 'spent_units', wanted)
  if budget > 0 and spent >= budget then
    exhausted = 1
    redis.call('HSET', KEYS[1], 'exhausted_at', now)
    if grace > 0 then
      local ttl = redis.call('PTTL', KEYS[1])
      if ttl < 0 or ttl > grace then
        redis.call('PEXPIRE', KEYS[1], grace)
      end
    end
  end
end
return {
  exhausted,
  redis.call('HGET', KEYS[1], 'pod_id') or '',
  redis.call('HGET', KEYS[1], 'path') or '',
  redis.call('HGET', KEYS[1], 'created_by_user_id') or '',
  redis.call('HGET', KEYS[1], 'minted_by_workload') or '',
  redis.call('HGET', KEYS[1], 'content_type') or '',
  redis.call('HGET', KEYS[1], 'filename') or ''
}
"""


# Write a link's claims, unless it has been revoked. KEYS[1] is the entry and
# KEYS[2] the revocation tombstone; both are examined and written in one step so
# a revoke cannot land between the check and the write.
#
# An entry already in the current shape is left alone, counters and all. Two
# fetches can both find nothing cached and both rehydrate; the second used to
# overwrite the first's entry after it had been charged, handing the link a
# fresh budget. Only an entry from before links went live is replaced.
#
# `object_key` through `spent_bytes` are what a worker from before links went
# live reads (`budget_bytes`/`spent_bytes`, and the object to stream). Written
# so that during a rolling deploy an old worker still serves the link, on a
# counter of its own, rather than 404ing every entry a new worker cached.
# Remove them in the release after this one.
CACHE_LUA = """
if redis.call('EXISTS', KEYS[2]) == 1 then
  return 0
end
if redis.call('HEXISTS', KEYS[1], 'budget_units') == 1 then
  return 1
end
redis.call('DEL', KEYS[1])
redis.call('HSET', KEYS[1],
  'pod_id', ARGV[1],
  'path', ARGV[2],
  'created_by_user_id', ARGV[3],
  'minted_by_workload', ARGV[4],
  'content_type', ARGV[5],
  'filename', ARGV[6],
  'max_hits', ARGV[7],
  'budget_units', ARGV[8],
  'spent_units', 0,
  'embedded_budget_bytes', ARGV[9],
  'embedded_spent_bytes', 0,
  'object_key', ARGV[10],
  'content_sha256', ARGV[11],
  'size_bytes', ARGV[12],
  'budget_bytes', ARGV[13],
  'spent_bytes', 0
)
redis.call('PEXPIREAT', KEYS[1], ARGV[14])
return 1
"""
