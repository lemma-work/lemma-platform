#!/usr/bin/env python3
"""The two ways to run Lemma locally must relax the same auth gates.

`lemma-stack` renders the environment for the Docker local stack and the
desktop install; the root `Makefile` builds it for `make dev`. Both are "Lemma
on your own machine", and both face the same fact: the gates that protect a
public deployment from strangers protect nothing on localhost, and several of
them actively obstruct the person developing.

They disagreed. `lemma-stack` turned off five; `make dev` turned off one. So the
documented way to run Lemma from a checkout refused an `@example.com` signup as
undeliverable, and locked the developer out of their own laptop for four minutes
after the sixth account in fifteen minutes -- while the Docker stack, the
desktop install, the e2e suite and the load tests all had none of that.

This does not ask anyone to keep one list. It asks that the lists say the same
thing, and it fails by naming the file that is behind.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

MAKEFILE = ROOT / "Makefile"
STACK_RENDER = ROOT / "lemma-stack/lemma_stack/config/render.py"
CONFIG = ROOT / "lemma-backend/app/core/config.py"
FRONTEND_CONFIG = ROOT / "lemma-harness/components/auth/portal/auth/config.ts"


def gates_on_by_default():
    """`auth_*` booleans a deployment gets whether or not it asks for them.

    The comparison is scoped to these deliberately. An `AUTH_*` setting that is
    already `False` by default costs nothing to leave unset, and a routing value
    like `AUTH_WEBSITE_BASE_PATH` is not a gate at all -- neither can lock
    anybody out of their own machine. A gate that is *on* unless disabled is the
    only kind where inheriting the default and rendering it explicitly differ.
    """
    text = CONFIG.read_text(encoding="utf-8")
    found = re.findall(
        r"^    (auth_\w+): bool = Field\(\n\s*default=(True|False),", text, re.MULTILINE
    )
    return {name.upper() for name, default in found if default == "True"}


def makefile_gates():
    """The gates `make dev` relaxes, from `DEV_LOCAL_AUTH_ENV`."""
    text = MAKEFILE.read_text(encoding="utf-8")
    match = re.search(
        r"^DEV_LOCAL_AUTH_ENV := \\\n((?:\t.*\\\n)*\t.*)$", text, re.MULTILINE
    )
    if not match:
        raise SystemExit(f"DEV_LOCAL_AUTH_ENV not found in {MAKEFILE}")
    pairs = re.findall(r"(AUTH_[A-Z_]+)=(\S+)", match.group(1))
    if not pairs:
        raise SystemExit(f"DEV_LOCAL_AUTH_ENV names no AUTH_* gates in {MAKEFILE}")
    return {key: value.rstrip("\\").strip() for key, value in pairs}


def stack_gates(gates):
    """The gates the local stack relaxes, from its rendered backend env."""
    text = STACK_RENDER.read_text(encoding="utf-8")
    pairs = re.findall(r'"(AUTH_[A-Z_]+)":\s*"([^"]+)"', text)
    if not pairs:
        raise SystemExit(f"no AUTH_* settings found in {STACK_RENDER}")
    return {key: value for key, value in pairs if key in gates}


def browser_mirrored_gates():
    """Gates the *browser* also reads, from the frontend's own auth config.

    A gate the frontend mirrors has two renderings to keep in step, not one, and
    the second is easy to miss precisely because the backend half looks complete.
    `supertokens-auth-react` mounts its email-verification recipe from this copy
    and defaults it to ON when unset, so a backend told to relax the gate and a
    frontend never told anything disagree -- and the disagreement is not a
    warning, it is a 404 on a route the backend never registered and a sign-in
    nobody can get past.
    """
    text = FRONTEND_CONFIG.read_text(encoding="utf-8")
    return {
        name
        for name in re.findall(r'"(AUTH_[A-Z_]+)",\s*\n\s*process\.env\.NEXT_PUBLIC_', text)
    }


def frontend_env_gates(gates):
    """What `make init` writes into the frontend's env, as NEXT_PUBLIC_ keys."""
    text = MAKEFILE.read_text(encoding="utf-8")
    written = set(re.findall(r"NEXT_PUBLIC_(AUTH_[A-Z_]+)", text))
    return {name for name in written if name in gates}

def main() -> int:
    gates = gates_on_by_default()
    if not gates:
        raise SystemExit(f"no default-on auth gates found in {CONFIG}")
    make = {k: v for k, v in makefile_gates().items() if k in gates}
    stack = stack_gates(gates)

    problems = []
    for key in sorted(set(stack) - set(make)):
        problems.append(
            f"  {MAKEFILE.name} does not set {key}, which {STACK_RENDER.name} "
            f"sets to {stack[key]!r} for a local install"
        )
    for key in sorted(set(make) - set(stack)):
        problems.append(
            f"  {STACK_RENDER.name} does not set {key}, which {MAKEFILE.name} "
            f"sets to {make[key]!r} for `make dev`"
        )
    for key in sorted(set(make) & set(stack)):
        if make[key] != stack[key]:
            problems.append(
                f"  {key}: {MAKEFILE.name} says {make[key]!r}, "
                f"{STACK_RENDER.name} says {stack[key]!r}"
            )

    # The browser's half. A gate the frontend mirrors has to be rendered into
    # the frontend's env too, or the backend relaxes it and the browser does not
    # -- which is not a mismatch anybody sees in a config file, it is a sign-in
    # that dead-ends on a route the backend was told not to register.
    mirrored = browser_mirrored_gates()
    for key in sorted(mirrored & set(make)):
        if key not in frontend_env_gates(mirrored):
            problems.append(
                f"  {key}: {MAKEFILE.name} relaxes it for the backend but never "
                f"writes NEXT_PUBLIC_{key} for the frontend, which reads the same "
                f"gate and defaults it ON"
            )

    if problems:
        print("Local auth gates disagree between `make dev` and the local stack:")
        print("\n".join(problems), file=sys.stderr)
        return 1

    print(
        f"Local auth gates agree on {sorted(make)}"
        + (f"; {sorted(mirrored)} also rendered for the browser." if mirrored else ".")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
