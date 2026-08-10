# shellcheck shell=sh
# Make `gh` authenticate as whatever account `git` is already using.
#
# The credential bridge writes one file per session for git's `store` helper
# (app/modules/agent/tools/workspace_cli/github_credential_bridge.py). `gh`
# does not read that file -- it wants GH_TOKEN, or its own hosts.yml -- so
# without this an agent gets working `git push` and a `gh` that claims it is
# not logged in, which is a confusing place to land.
#
# Derived here rather than written as a second file so the token exists in
# exactly one place on disk, and so revoking it means deleting one thing.
# Commands run through `bash -lc`, so this is evaluated per command and picks
# up a credential the bridge provisioned after the sandbox started.
if [ -r /tmp/.git-credentials ]; then
    _lemma_gh_token="$(
        sed -n 's|^https://x-access-token:\([^@]*\)@github\.com.*|\1|p' \
            /tmp/.git-credentials | head -n 1
    )"
    if [ -n "${_lemma_gh_token}" ]; then
        GH_TOKEN="${_lemma_gh_token}"
        export GH_TOKEN
    fi
    unset _lemma_gh_token
fi

# An agent's shell is not a terminal anyone is watching: a background version
# check just adds latency and stray stderr to every invocation.
GH_NO_UPDATE_NOTIFIER=1
export GH_NO_UPDATE_NOTIFIER
GH_PAGER=cat
export GH_PAGER
