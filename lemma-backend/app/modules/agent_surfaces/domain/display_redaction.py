"""Removing credentials from text that is about to be shown in somebody else's chat.

The arguments of a paused tool call are model-written and arbitrary: a token can
sit under any key, several objects deep, inside a JSON document that was itself
stored as a string, or inside a shell command. Once the text is on a phone it is
in external chat history and cannot be recalled, so this errs toward hiding: a
value that merely *looks* like a credential is hidden too.

Two independent nets, because neither is enough alone:

* **by name** -- a key that reads like a credential hides whatever is under it,
  whatever its shape (including a whole nested object);
* **by shape** -- a value that looks like a credential is hidden wherever it
  appears, whatever it is called (a bearer header in a command, a provider key
  prefix, a URL with a password in it, a long random-looking token).

Redaction always runs on the structure *before* it is serialised, joined or cut
to length, so a secret is never split across a truncation boundary.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Mapping

MASK = "***"

_MAX_DEPTH = 8
_MAX_ITEMS = 24
_MAX_JSON_STRING_CHARS = 20_000

# Substrings that make a key a credential wherever they occur in it.
_SENSITIVE_SUBSTRING = (
    "passw",
    "pswd",
    "pwd",
    "passphrase",
    "passcode",
    "secret",
    "token",
    "credential",
    "authoriz",
    "bearer",
    "cookie",
    "session",
    "auth",
    "jwt",
    "signature",
    "apikey",
    "secretkey",
    "accesskey",
    "privatekey",
    "signingkey",
    "encryptionkey",
    "masterkey",
    "sshkey",
    "authkey",
    "clientkey",
)
# Short words that would be false positives as substrings ("spin", "origin"),
# so they only count when they are a whole word of the key.
_SENSITIVE_WORDS = frozenset({"pin", "otp", "sig", "sid", "pass", "pw", "salt", "cert"})

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")


def is_sensitive_key(key: object) -> bool:
    """True when a key's *name* says its value is a credential."""
    spaced = _CAMEL_BOUNDARY.sub("_", str(key))
    words = [word for word in _NON_ALNUM.split(spaced.lower()) if word]
    compact = "".join(words)
    if any(part in compact for part in _SENSITIVE_SUBSTRING):
        return True
    return any(word in _SENSITIVE_WORDS for word in words)


# The same idea for a key found *inside* a string (a command, a JSON fragment,
# an env assignment). Camel-case words cannot be split here, so the short words
# are matched only when delimited.
_TEXT_KEY = (
    r"(?:passw|pswd|pwd|pw|passphrase|passcode|secret|token|credential|authoriz|"
    r"bearer|cookie|session|auth|jwt|signature|api[_\-]?key|access[_\-]?key|"
    r"private[_\-]?key|signing[_\-]?key|encryption[_\-]?key|master[_\-]?key|"
    r"ssh[_\-]?key|secret[_\-]?key)[\w.\-]*"
    r"|(?<![A-Za-z])(?:pin|otp|sig|sid|pass|salt|cert)(?![A-Za-z])[\w.\-]*"
)
# A quoted value (the quotes may themselves be backslash-escaped because the
# fragment was JSON inside a JSON string), or a bare word, which for an
# Authorization-style header also owns the scheme word before it.
_TEXT_VALUE = (
    r"""\{.*?\}|\[.*?\]|\\*"[^"]*?\\*"|\\*'[^']*?\\*'"""
    r"""|(?:(?:bearer|basic|digest|token)\s+)?[^\s,;&}\]'"\\)]+"""
)
_KEY_ASSIGNMENT = re.compile(
    rf"(?P<key>{_TEXT_KEY})(?P<quote>\\*[\"']?)(?P<sep>\s*[:=]\s*)(?P<value>{_TEXT_VALUE})",
    re.IGNORECASE,
)
# `--password hunter2`, `--api-key=hunter2`: a flag names the credential and the
# next word is its value.
_SENSITIVE_FLAG = re.compile(
    rf"(?P<flag>(?<![\w-])--?[\w-]*(?:{_TEXT_KEY}))(?P<sep>[=\s]+)"
    rf"(?P<value>{_TEXT_VALUE})",
    re.IGNORECASE,
)
# `mysql -pSECRET` and `sshpass -p SECRET`. `-p` is also "parents" and "port",
# so a purely numeric or path-shaped value is left alone; anything else is
# hidden, because guessing wrong the other way puts a password in the chat.
_SHORT_PASSWORD_FLAG = re.compile(
    r"(?P<flag>(?<![\w-])-p)(?P<sep>\s*)(?P<value>[^\s'\"-][^\s'\"]*|'[^']*'|\"[^\"]*\")"
)
# `curl -u user:secret`.
_USER_PASSWORD_FLAG = re.compile(
    r"(?P<flag>(?<![\w-])(?:-u|--user))(?P<sep>[=\s]+)(?P<value>[^\s'\"]*:[^\s'\"]+)"
)
_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|$)",
    re.DOTALL,
)
# `postgres://user:secret@host`, `https://token@host`.
_URL_USERINFO = re.compile(r"(?P<scheme>\b[a-z][a-z0-9+.\-]*://)[^\s/@'\"]+@", re.I)
_BEARER = re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=\-]{6,}")
# Credentials that identify themselves by their prefix.
_KNOWN_PREFIX = re.compile(
    r"\b(?:"
    r"sk-[A-Za-z0-9_\-]{8,}|"
    r"(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{8,}|"
    r"xox[a-z]-[A-Za-z0-9\-]{6,}|"
    r"gh[pousr]_[A-Za-z0-9]{12,}|github_pat_[A-Za-z0-9_]{12,}|"
    r"glpat-[A-Za-z0-9_\-]{12,}|npm_[A-Za-z0-9]{16,}|pypi-[A-Za-z0-9_\-]{16,}|"
    r"AKIA[A-Z0-9]{12,}|ASIA[A-Z0-9]{12,}|AIza[A-Za-z0-9_\-]{16,}|"
    r"SG\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}|"
    r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]*"
    r")"
)
# Anything else long, mixed and random-looking.
_LONG_TOKEN = re.compile(r"(?<![\w+/=\-.])[A-Za-z0-9_\-+/=]{24,}(?![\w+/=\-])")
_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)
_MIN_TOKEN_ENTROPY = 3.6


def _entropy(text: str) -> float:
    counts = Counter(text)
    return -sum((n / len(text)) * math.log2(n / len(text)) for n in counts.values())


def _mask_long_token(match: re.Match[str]) -> str:
    token = match.group(0)
    if _UUID.match(token):
        return token
    has_digit = any(ch.isdigit() for ch in token)
    has_alpha = any(ch.isalpha() for ch in token)
    # A path or a long snake_case name is not random: it has no digits, or it
    # has separators doing the work of structure.
    if not (has_digit and has_alpha):
        return token
    if "/" in token and token.count("/") > 2:
        return token
    return MASK if _entropy(token) >= _MIN_TOKEN_ENTROPY else token


def _mask_short_password_flag(match: re.Match[str]) -> str:
    value = match.group("value")
    if value.isdigit() or value.startswith(("/", ".", "~")):
        return match.group(0)
    return f"{match.group('flag')}{match.group('sep')}{MASK}"


def _mask_assignment(match: re.Match[str]) -> str:
    # Keep the value's own quotes so a masked JSON fragment still reads as JSON.
    opening = re.match(r"\\*[\"']", match.group("value"))
    masked = f"{opening.group(0)}{MASK}{opening.group(0)}" if opening else MASK
    return f"{match.group('key')}{match.group('quote')}{match.group('sep')}{masked}"


def redact_secrets_in_text(text: str) -> str:
    """The text with everything credential-shaped in it replaced by ``***``."""
    if not text:
        return text
    redacted = _PRIVATE_KEY_BLOCK.sub(MASK, text)
    redacted = _URL_USERINFO.sub(lambda m: f"{m.group('scheme')}{MASK}@", redacted)
    redacted = _BEARER.sub(MASK, redacted)
    redacted = _KNOWN_PREFIX.sub(MASK, redacted)
    redacted = _USER_PASSWORD_FLAG.sub(
        lambda m: f"{m.group('flag')}{m.group('sep')}{MASK}", redacted
    )
    redacted = _SENSITIVE_FLAG.sub(
        lambda m: f"{m.group('flag')}{m.group('sep')}{MASK}", redacted
    )
    redacted = _SHORT_PASSWORD_FLAG.sub(_mask_short_password_flag, redacted)
    redacted = _KEY_ASSIGNMENT.sub(_mask_assignment, redacted)
    return _LONG_TOKEN.sub(_mask_long_token, redacted)


def _parsed_json_container(text: str) -> dict[str, object] | list[object] | None:
    """The object or array a string holds, when the whole string is JSON."""
    stripped = text.strip()
    if (
        not stripped
        or stripped[0] not in "{["
        or len(stripped) > _MAX_JSON_STRING_CHARS
    ):
        return None
    try:
        parsed = json.loads(stripped)
    except ValueError:
        return None
    return parsed if isinstance(parsed, (dict, list)) else None


def redact_structure(
    value: object, *, key: object | None = None, depth: int = 0
) -> object:
    """A copy of ``value`` with every credential in it masked, at any depth.

    Dicts, lists and JSON documents stored as strings are walked; a sensitive
    key masks its whole value; every remaining string gets the by-shape pass.
    Past the depth or width cap the rest is dropped rather than shown.
    """
    if key is not None and is_sensitive_key(key):
        return MASK
    if depth > _MAX_DEPTH:
        return "…"
    if isinstance(value, Mapping):
        items = list(value.items())
        out = {
            redact_secrets_in_text(str(k)): redact_structure(v, key=k, depth=depth + 1)
            for k, v in items[:_MAX_ITEMS]
        }
        if len(items) > _MAX_ITEMS:
            out["…"] = f"{len(items) - _MAX_ITEMS} more"
        return out
    if isinstance(value, (list, tuple, set, frozenset)):
        elements = list(value)
        shown = [redact_structure(v, depth=depth + 1) for v in elements[:_MAX_ITEMS]]
        if len(elements) > _MAX_ITEMS:
            shown.append("…")
        return shown
    if isinstance(value, str):
        nested = _parsed_json_container(value)
        if nested is not None:
            return redact_structure(nested, depth=depth + 1)
        return redact_secrets_in_text(value)
    if isinstance(value, (bytes, bytearray)):
        return MASK
    return value
