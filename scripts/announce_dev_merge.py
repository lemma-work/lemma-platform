#!/usr/bin/env python3
"""Post a platform merge and its release action to #lemma-releases."""

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request


CHANNEL = "C0C3JT0AHPH"


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def slack_text(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def post(token: str, payload: dict) -> dict:
    request = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        result = json.load(response)
    if not result.get("ok"):
        raise RuntimeError(f"Slack post failed: {result.get('error', 'unknown')}")
    return result


def main() -> None:
    token = os.environ["SLACK_RELEASE_BOT_TOKEN"]
    repo = os.environ["GITHUB_REPOSITORY"]
    sha = os.environ["GITHUB_SHA"]
    before = os.environ.get("MERGE_BEFORE", "")
    short = sha[:7]
    subject = slack_text(git("log", "-1", "--format=%s", sha))
    author = slack_text(git("log", "-1", "--format=%an", sha))
    commit_url = f"https://github.com/{repo}/commit/{sha}"
    compare_url = (
        f"https://github.com/{repo}/compare/{before}...{sha}"
        if before and before != "0" * 40
        else commit_url
    )
    root = post(
        token,
        {
            "channel": CHANNEL,
            "text": f"Platform merged: {subject} ({short}). Development release is ready to review.",
            "unfurl_links": False,
            "blocks": [
                {"type": "header", "text": {"type": "plain_text", "text": "Platform merge is ready for development", "emoji": True}},
                {"type": "section", "text": {"type": "mrkdwn", "text": f"*{subject}*\n<{commit_url}|`{short}`> · merged by {author}"}},
                {"type": "context", "elements": [{"type": "mrkdwn", "text": f"*Source*  `lemma-platform/main`   •   <{compare_url}|Review changes>   •   *Target*  development"}]},
                {"type": "divider"},
                {"type": "section", "text": {"type": "mrkdwn", "text": "The release thread will show the app pin PR, image build, deployment PR, migration hold if needed, and rollout result."}},
            ],
        },
    )
    actions = [{"type": "button", "text": {"type": "plain_text", "text": "View merge", "emoji": True}, "url": commit_url}]
    if os.environ.get("SLACK_RELEASE_BUTTON_ENABLED") == "true":
        actions.insert(0, {"type": "button", "action_id": "release_dev", "text": {"type": "plain_text", "text": "Release to development", "emoji": True}, "style": "primary", "value": sha})
        status = "*Ready when you are.* The release checks the migration range before it changes the app pin. Schema changes pause for review."
    else:
        status = "*Merge recorded.* The one-click release action will appear after the signed bridge and GitHub permissions are connected."
    post(
        token,
        {
            "channel": CHANNEL,
            "thread_ts": root["ts"],
            "text": f"Platform merge {short} recorded for development",
            "blocks": [
                {"type": "section", "text": {"type": "mrkdwn", "text": status}},
                {"type": "actions", "elements": actions},
            ],
        },
    )
    print(f"Posted {sha} to #{CHANNEL} thread {root['ts']}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, urllib.error.URLError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Release announcement failed: {error}", file=sys.stderr)
        raise SystemExit(1)
