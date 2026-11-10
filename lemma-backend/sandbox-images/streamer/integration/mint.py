"""Mint a stream ticket the way the API does: b64url(json) "." b64url(hmac)."""
import base64, hashlib, hmac, json, sys, time, uuid

def b64(b): return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

key = bytes.fromhex(sys.argv[1]); machine = sys.argv[2]; mode = sys.argv[3]
exp = int(time.time()) + int(sys.argv[4] if len(sys.argv) > 4 else 60)
payload = b64(json.dumps({"sub": "it-" + mode, "machine": machine, "mode": mode, "exp": exp, "jti": uuid.uuid4().hex}, separators=(",", ":")).encode())
print(payload + "." + b64(hmac.new(key, payload.encode(), hashlib.sha256).digest()))
