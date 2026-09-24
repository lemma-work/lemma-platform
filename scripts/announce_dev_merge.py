#!/usr/bin/env python3
"""Post a platform merge and its release action to #lemma-releases."""

import json
import os
import re
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
    short = sha[:7]
    subject = git("log", "-1", "--format=%s", sha)
    author_name = git("log", "-1", "--format=%an", sha)
    author = slack_text(author_name)
    commit_url = f"https://github.com/{repo}/commit/{sha}"
    pr_match = re.search(r" \(#(\d+)\)$", subject)
    title = subject[: pr_match.start()] if pr_match else subject
    heading = title if len(title) <= 150 else title[:147] + "..."
    review_url = f"https://github.com/{repo}/pull/{pr_match.group(1)}" if pr_match else commit_url
    review_label = f"PR #{pr_match.group(1)}" if pr_match else "Commit"
    actions = [{"type": "button", "text": {"type": "plain_text", "text": "Review changes", "emoji": True}, "url": review_url}]
    if os.environ.get("SLACK_RELEASE_BUTTON_ENABLED") == "true":
        actions.insert(0, {"type": "button", "action_id": "release_dev", "text": {"type": "plain_text", "text": "Release to development", "emoji": True}, "style": "primary", "value": sha})
    post(
        token,
        {
            "channel": CHANNEL,
            "text": (
                f"{title}. {review_label}: {review_url}. By {author_name}. "
                f"Commit {short}: {commit_url}. Merged to lemma-platform/main; "
                "release target: development."
            ),
            "unfurl_links": False,
            "blocks": [
                {"type": "header", "text": {"type": "plain_text", "text": heading, "emoji": True}},
                {"type": "context", "elements": [{"type": "mrkdwn", "text": f"<{review_url}|{review_label}>  ·  by {author}  ·  <{commit_url}|`{short}`>  ·  `lemma-platform/main` → *Development*"}]},
                {"type": "actions", "elements": actions},
            ],
        },
    )
    print(f"Posted {sha} to #{CHANNEL}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, urllib.error.URLError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Release announcement failed: {error}", file=sys.stderr)
        raise SystemExit(1)
