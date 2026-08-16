"""The script one `execute_python` call runs, as text.

E2B has no resident interpreter to hold a session, so continuity is rebuilt
from what it does offer: each call is a fresh `python3`, and the namespace
travels between calls in a file beside the sandbox's tmp state. That makes this
string the whole of the REPL, which is why it lives on its own -- it is
executed by a different interpreter than the one that formats it, so nothing
here can be checked by the type checker or exercised by importing it.
"""

from __future__ import annotations

_PYTHON_RUNNER = """
import ast, importlib, pickle, os, sys, types

_STATE = {state_path!r}
_MODULES = _STATE + ".modules"
_CODE = {code_path!r}
_RESULT = {result_path!r}

_ns = {{"__name__": "__main__"}}
if os.path.exists(_STATE):
    try:
        with open(_STATE, "rb") as handle:
            _ns.update(pickle.load(handle))
    except Exception:
        pass
# A module cannot be pickled, so the namespace filter below drops every one of
# them -- which quietly broke the only thing this tool promises. `import pandas
# as pd` in one call left `pd` undefined in the next, and the agent saw a bare
# NameError for a name it had just bound. Modules are carried by name and
# re-imported instead: the import is cached by the interpreter anyway, so this
# restores the binding rather than repeating the work.
if os.path.exists(_MODULES):
    try:
        with open(_MODULES, "rb") as handle:
            for _alias, _module_name in pickle.load(handle).items():
                try:
                    _ns[_alias] = importlib.import_module(_module_name)
                except Exception:
                    # Uninstalled since the last call. Leaving the name unbound
                    # gives the agent the real ImportError when it is used.
                    pass
    except Exception:
        pass

with open(_CODE) as handle:
    _source = handle.read()

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
        with open(_STATE, "wb") as handle:
            pickle.dump(_keep, handle)
        with open(_MODULES, "wb") as handle:
            pickle.dump(_module_names, handle)
    except Exception:
        pass
"""
