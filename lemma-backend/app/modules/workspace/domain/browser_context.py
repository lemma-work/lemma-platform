"""Which browser a caller means. There is one, and this says so.

There used to be three kinds of browser in a sandbox and a function here to
name two of them: `agent_session(conversation_id)` gave each conversation its
own Chrome, and the relay named a third per site for a sign-in to happen in.
Each was a separate profile directory, because Chrome locks the one it opens.

All three are now one, and the reason is what a login is for. A sign-in
captured in a site's browser had to be carried into the conversation's
browser, and reconstructing it there meant deciding which cookies *were* the
login -- a guess that was wrong in a different way three times, and the last
time told the person their login had been kept when what had been kept was a
cookie banner's consent flag. A browser that keeps its own profile has nothing
to carry and nothing to guess.

What that trades away, stated plainly because it was a real property: a
conversation no longer has cookies of its own. Signing in to a site in one
conversation signs the person in for every later one, without being asked
again. That is how the browser on their own desk behaves, and the sandbox was
already one machine per person -- an agent with a shell in it could always
*use* any session that existed. What changes is that the session now outlives
the conversation that created it.

An agent that genuinely needs two browsers at once -- two accounts on one site,
side by side -- still passes `--session` with a `--profile` of its own, and
those stay under `/tmp` where they die with the sandbox. That is the escape
hatch, and it is deliberately the noisy one.
"""

from __future__ import annotations

#: The session every browser command lands in, and the one the image's own
#: `AGENT_BROWSER_SESSION` names. A shell in the sandbox inherits it, so the
#: agent's `agent-browser` in `exec_command`, the relay, and the pane a person
#: watches are all looking at the same Chrome without anyone passing a name.
DEFAULT_SESSION = "workspace"


__all__ = [
    "DEFAULT_SESSION",
]
