"""The script one `execute_python` call runs, as text.

E2B has no resident interpreter to hold a session, so continuity is rebuilt
from what it does offer: each call is a fresh interpreter, and the namespace
travels between calls in a file in the session's own directory. That makes this
string the whole of the REPL, which is why it lives on its own -- it is
executed by a different interpreter than the one that formats it, so nothing
here can be checked by the type checker or exercised by importing it.

**Unpickling runs code**, so the saved namespace is sealed: an HMAC over the
pickle, keyed per session by the backend (`e2b_python_sessions`), checked before
anything is loaded. A file the runner did not write for this session -- planted
by another session, or by anything else in the sandbox -- fails the check and is
discarded unread. The key arrives inside this script, which deletes itself
before it does anything else, so it sits on disk only for as long as the
interpreter takes to start.

Every process in the sandbox runs as the same Linux user, so this raises the
bar rather than closing the door: something already watching for this file
could read the key in that window. A user per actor is what would close it.
"""

from __future__ import annotations

PYTHON_RUNNER = """
import ast, hashlib, hmac, importlib, os, pickle, sys, types

_KEY = {key!r}.encode()
_STATE = {state_path!r}
_CODE = {code_path!r}
_RESULT = {result_path!r}
_USER_SITE = {user_site!r}

# First, so the key is on disk for as short a time as it can be.
try:
    os.unlink(__file__)
except OSError:
    pass


def _seal(payload):
    return hmac.new(_KEY, payload, hashlib.sha256).digest() + payload


def _unseal(blob):
    mac, payload = blob[:32], blob[32:]
    expected = hmac.new(_KEY, payload, hashlib.sha256).digest()
    return payload if len(blob) > 32 and hmac.compare_digest(mac, expected) else None


# Started with `-I`, so the imports above came from the image alone: not the
# working directory, not the home's site-packages, not a `PYTHON*` variable.
# The user's code gets the path a plain `python` would have given it, ahead of
# the unpickling below -- a saved value may be an instance of a class the user
# installed.
sys.path[:0] = [_p for _p in (os.getcwd(), _USER_SITE) if _p not in sys.path]

_saved = {{}}
if os.path.exists(_STATE):
    try:
        with open(_STATE, "rb") as handle:
            _payload = _unseal(handle.read())
        if _payload is None:
            print(
                "lemma: this session's saved variables failed their integrity "
                "check and were discarded",
                file=sys.stderr,
            )
        else:
            _saved = pickle.loads(_payload)
    except Exception:
        pass

_ns = {{"__name__": "__main__"}}
_ns.update(_saved.get("names", {{}}))
# A module cannot be pickled, so the namespace filter below drops every one of
# them -- which quietly broke the only thing this tool promises. `import pandas
# as pd` in one call left `pd` undefined in the next, and the agent saw a bare
# NameError for a name it had just bound. Modules are carried by name and
# re-imported instead: the import is cached by the interpreter anyway, so this
# restores the binding rather than repeating the work.
for _alias, _module_name in _saved.get("modules", {{}}).items():
    try:
        _ns[_alias] = importlib.import_module(_module_name)
    except Exception:
        # Uninstalled since the last call. Leaving the name unbound gives the
        # agent the real ImportError when it is used.
        pass

with open(_CODE) as handle:
    _source = handle.read()
try:
    os.unlink(_CODE)
except OSError:
    pass

_tree = ast.parse(_source)
_tail = None
if _tree.body and isinstance(_tree.body[-1], ast.Expr):
    _tail = ast.Expression(_tree.body.pop().value)

try:
    exec(compile(_tree, "<session>", "exec"), _ns)
    if _tail is not None:
        _value = eval(compile(_tail, "<session>", "eval"), _ns)
        if _value is not None:
            with open(_RESULT, "w") as handle:
                handle.write(repr(_value) if not isinstance(_value, str) else _value)
finally:
    _keep = {{}}
    _module_names = {{}}
    for _name, _value in list(_ns.items()):
        if _name.startswith("__"):
            continue
        if isinstance(_value, types.ModuleType):
            _module_names[_name] = _value.__name__
            continue
        try:
            pickle.dumps(_value)
        except Exception:
            continue
        _keep[_name] = _value
    try:
        _blob = _seal(pickle.dumps({{"names": _keep, "modules": _module_names}}))
        # Through a rename, so a call that dies half-way leaves the last whole
        # state rather than a torn one that fails its check.
        _staged = _STATE + ".tmp"
        with open(os.open(_staged, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as handle:
            handle.write(_blob)
        os.replace(_staged, _STATE)
    except Exception:
        pass
"""
