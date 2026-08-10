# shellcheck shell=sh
# Shell defaults for `gh` in a workspace.
#
# The credential itself is not here. `GH_CONFIG_DIR` points at the directory
# the credential bridge writes `hosts.yml` into
# (app/modules/agent/tools/workspace_cli/github_credential_bridge.py), so `gh`
# authenticates as the same account `git` does without the token ever entering
# the environment. Exporting GH_TOKEN would put it in the environment of every
# process the agent starts, where an ordinary `env` prints it into a tool
# result and the transcript, and any subprocess -- an npm postinstall hook, a
# test harness -- inherits it.
#
# /tmp, like git's credential file: a session-scoped credential must not
# survive on the durable /workspace volume.
GH_CONFIG_DIR=/tmp/lemma-gh
export GH_CONFIG_DIR

# An agent's shell is not a terminal anyone is watching: a background version
# check just adds latency and stray stderr, and a pager waits for a keypress
# that never comes.
GH_NO_UPDATE_NOTIFIER=1
export GH_NO_UPDATE_NOTIFIER
GH_PAGER=cat
export GH_PAGER
