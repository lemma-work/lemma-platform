"""GitHub as a webhook source.

One App has one webhook URL and the installation decides which repositories it
covers, so everything arrives here: every event type, for every organization
that installed it. What separates them is the routing key, not the endpoint.

The key is `{source, installation_id, event}`. `installation_id` is what makes
it tenant-scoped -- without it a `pull_request` schedule in one organization
matches another organization's pull requests -- and the three together are
selective enough to reuse the existing GIN index on the schedule config with no
schema change. `repository_id` narrows further when the author scoped a
schedule to one repository, and `actions` filters here.

Repository *ids* rather than names throughout: a repository can be renamed or
transferred, and a schedule that stops firing when someone renames a repo is a
bug nobody connects to the rename.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable

from app.core.log.log import get_logger
from app.core.webhooks.signatures import hex_digest_signature_matches
from app.modules.connectors.config import connector_settings
from app.modules.schedule.contracts import (
    NormalizedWebhook,
    VerifiedDelivery,
    WebhookDelivery,
    WebhookNotVerified,
    WebhookPayload,
)

logger = get_logger(__name__)

SIGNATURE_HEADER = "X-Hub-Signature-256"
EVENT_HEADER = "X-GitHub-Event"

# The events the catalog offers triggers for. An event outside this set is
# acknowledged and dropped: an App subscribes to events at the App level, so a
# deployment can be receiving `star` deliveries nobody asked for, and a non-2xx
# answer to those would have GitHub disable the hook for the events that matter.
SUPPORTED_EVENTS = frozenset(
    {
        "push",
        "pull_request",
        "pull_request_review",
        "pull_request_review_comment",
        "issues",
        "issue_comment",
        "workflow_run",
        "check_suite",
        "check_run",
        "release",
    }
)

# Events whose subject is one pull request, and so whose branch is its head.
_PULL_REQUEST_EVENTS = frozenset(
    {"pull_request", "pull_request_review", "pull_request_review_comment"}
)


def _push_key(payload: WebhookPayload) -> str | None:
    # `after` is the commit the ref now points at: the same push redelivered
    # names the same commit, and a force-push to a different one is a different
    # event that should fire again.
    return payload.get("after")


def _pull_request_key(payload: WebhookPayload) -> str | None:
    pull_request = payload.get("pull_request") or {}
    head_sha = (pull_request.get("head") or {}).get("sha")
    return f"{pull_request.get('id')}:{payload.get('action')}:{head_sha}"


def _review_key(payload: WebhookPayload) -> str | None:
    # A review is submitted once and dismissed once, but its body can be edited
    # any number of times, and the body is the only thing an edit changes -- a
    # review carries no `updated_at`. Without it the second edit would collapse
    # onto the first and never fire.
    review = payload.get("review") or {}
    return (
        f"{review.get('id')}:{payload.get('action')}:{review.get('state')}"
        f":{review.get('body')}"
    )


def _review_comment_key(payload: WebhookPayload) -> str | None:
    # `updated_at` moves on every edit, so each edit is its own event, while a
    # redelivery -- the same payload, byte for byte -- names the same instant.
    comment = payload.get("comment") or {}
    return f"{comment.get('id')}:{payload.get('action')}:{comment.get('updated_at')}"


def _issue_key(payload: WebhookPayload) -> str | None:
    # `issue_comment` carries both; the comment is the thing that happened.
    subject = payload.get("comment") or payload.get("issue") or {}
    return f"{subject.get('id')}:{payload.get('action')}"


def _workflow_run_key(payload: WebhookPayload) -> str | None:
    run = payload.get("workflow_run") or {}
    return f"{run.get('id')}:{run.get('status')}"


def _check_suite_key(payload: WebhookPayload) -> str | None:
    suite = payload.get("check_suite") or {}
    return f"{suite.get('id')}:{suite.get('status')}"


def _check_run_key(payload: WebhookPayload) -> str | None:
    # Unlike a suite, a run's status is not enough on its own: `rerequested`
    # and `requested_action` arrive for a run that has already completed, with
    # the same status and conclusion as the `completed` delivery before them,
    # and two different buttons on one run differ only by their identifier.
    # An App may also re-run a check in place, and it can finish with the
    # conclusion it had before; `completed_at` is what tells that second
    # completion from a redelivery of the first.
    run = payload.get("check_run") or {}
    button = (payload.get("requested_action") or {}).get("identifier")
    return (
        f"{run.get('id')}:{payload.get('action')}:{run.get('status')}"
        f":{run.get('conclusion')}:{run.get('completed_at')}:{button}"
    )


def _release_key(payload: WebhookPayload) -> str | None:
    release = payload.get("release") or {}
    return f"{release.get('id')}:{payload.get('action')}"


# Dict dispatch rather than a chain of `if event == ...`: one function per event
# stays under the complexity gate, and an event with no entry is a lookup miss
# rather than a fall-through into the wrong branch.
_EVENT_KEYS: dict[str, Callable[[WebhookPayload], str | None]] = {
    "push": _push_key,
    "pull_request": _pull_request_key,
    "pull_request_review": _review_key,
    "pull_request_review_comment": _review_comment_key,
    "issues": _issue_key,
    "issue_comment": _issue_key,
    "workflow_run": _workflow_run_key,
    "check_suite": _check_suite_key,
    "check_run": _check_run_key,
    "release": _release_key,
}


def source_event_id(
    event: str,
    installation_id: str | None,
    repository_id: object,
    payload: WebhookPayload,
) -> str | None:
    """A stable id for the *event*, not for the delivery.

    `X-GitHub-Delivery` is per-delivery: redelivering the same push -- from the
    App's advanced tab, or by GitHub's own retry -- issues a new one, and using
    it would run every matched schedule a second time for something that
    happened once.

    Derived from content instead, so a redelivery collapses onto the first
    attempt and any future transport (polling the events API, say) produces the
    same id for the same event.
    """
    key = _EVENT_KEYS.get(event)
    if key is None:
        return None
    event_key = key(payload)
    if not event_key:
        return None
    material = f"github:{event}:{installation_id}:{repository_id}:{event_key}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _candidate_secrets() -> list[str | None]:
    """The webhook secrets a delivery may be signed with.

    Two, so rotating one is not an outage. A rotation has both live for as long
    as it takes to update GitHub, and with a single secret that window is a
    stream of 403s indistinguishable from an attack -- which GitHub answers by
    disabling the hook.
    """
    return [
        _reveal(connector_settings.connector_github_app_webhook_secret),
        _reveal(
            getattr(
                connector_settings,
                "connector_github_app_webhook_secret_previous",
                None,
            )
        ),
    ]


def _reveal(secret: object) -> str | None:
    if secret is None:
        return None
    getter = getattr(secret, "get_secret_value", None)
    return getter() if callable(getter) else str(secret)


class GitHubWebhookSource:
    """A GitHub App's deliveries."""

    source = "github"

    async def verify(self, delivery: WebhookDelivery) -> VerifiedDelivery:
        import json

        secrets = _candidate_secrets()
        if not any(secrets):
            # Unconfigured, not unauthenticated. Accepting deliveries because no
            # secret is set would make the endpoint an open door that looks like
            # it is working.
            logger.warning(
                "schedule.webhook_sources.github.no_webhook_secret_configured.degraded"
            )
            raise WebhookNotVerified
        signature = delivery.header(SIGNATURE_HEADER)
        if not hex_digest_signature_matches(signature, delivery.raw_body, secrets):
            raise WebhookNotVerified
        try:
            payload = json.loads(delivery.raw_body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise WebhookNotVerified from exc
        if not isinstance(payload, dict):
            raise WebhookNotVerified
        return VerifiedDelivery(delivery=delivery, payload=payload)

    async def observe(self, verified: VerifiedDelivery) -> None:
        """Retire what an uninstalled App leaves behind.

        Uninstalling is the one GitHub-side change that invalidates everything
        at once: the installation stops existing, so no installation token can
        be minted and no delivery will ever arrive again. Left alone, the
        accounts go on saying CONNECTED and their schedules go on saying active,
        and the only symptom is that nothing happens -- which is indistinguishable
        from an agent with nothing to do.

        `suspend` is treated the same. A suspended installation issues no tokens
        and sends no deliveries; the difference from `deleted` is only that it
        can be undone, and reconnecting is how you undo it either way.
        """
        event = (verified.delivery.header(EVENT_HEADER) or "").strip().lower()
        action = verified.payload.get("action")
        if event != "installation" or action not in {"deleted", "suspend"}:
            return
        installation_id = (verified.payload.get("installation") or {}).get("id")
        if installation_id is None:
            return
        await _retire_installation(str(installation_id), action=str(action))

    def normalize(self, verified: VerifiedDelivery) -> NormalizedWebhook | None:
        payload = verified.payload
        event = (verified.delivery.header(EVENT_HEADER) or "").strip().lower()
        if event not in SUPPORTED_EVENTS:
            return None

        installation_id = (payload.get("installation") or {}).get("id")
        if installation_id is None:
            # Every App delivery carries one. Without it there is no tenant, and
            # matching on `{source, event}` alone would route one organization's
            # events into another's schedules.
            logger.warning(
                "schedule.webhook_sources.github.delivery_without_installation.degraded",
                github_event=event,
            )
            return None

        repository = payload.get("repository") or {}
        repository_id = repository.get("id")
        event_id = source_event_id(event, str(installation_id), repository_id, payload)
        if event_id is None:
            return None

        match: WebhookPayload = {
            "source": "github",
            "installation_id": str(installation_id),
            "event": event,
        }
        return NormalizedWebhook(
            payload=payload,
            source_event_id=event_id,
            match=match,
            refine=_narrower(repository_id, payload.get("action")),
            context=_repo_context(payload, event),
        )


def _ref_for(payload: WebhookPayload, event: str) -> str | None:
    """The branch the agent should be standing on.

    A pull request event -- including a review or a comment on its diff -- is
    about its *head*: an agent asked to review one and dropped on `main` is
    looking at the wrong code. Everything else is about wherever the
    repository's default branch is, which is what a clone gives you without
    asking.

    A pull request from a fork binds no branch. Its head lives in the fork,
    and the checkout clones the base repository: `--branch` with the fork's
    branch either fails the clone outright or, when the base happens to have a
    branch of the same name -- a fork's `main` -- silently checks out the
    wrong code. The default branch is at least the right repository, and
    `gh pr checkout` gets the head from there.
    """
    if event in _PULL_REQUEST_EVENTS:
        return _head_ref(payload.get("pull_request"), payload.get("repository"))
    if event == "push":
        # `refs/heads/topic` -> `topic`. A tag push gives `refs/tags/...`, which
        # this deliberately does not turn into a branch name.
        ref = str(payload.get("ref") or "")
        return (
            ref.removeprefix("refs/heads/") if ref.startswith("refs/heads/") else None
        )
    if event == "check_run":
        return _check_run_ref(payload)
    return None


def _check_run_ref(payload: WebhookPayload) -> str | None:
    """The branch holding the commit the run checked.

    An agent asked why a check failed needs the code that failed it. The
    suite's `head_branch` names that branch, and where it is null a pull
    request the run is attached to is the only other name for it. GitHub
    documents both as missing for a push to a fork, which binds no branch and
    leaves the clone on the default one.
    """
    run = payload.get("check_run") or {}
    branch = (run.get("check_suite") or {}).get("head_branch")
    if branch:
        return str(branch)
    pull_requests = run.get("pull_requests") or []
    if not pull_requests:
        return None
    return _head_ref(pull_requests[0], payload.get("repository"))


def _head_ref(pull_request: object, repository: object) -> str | None:
    """A pull request's head branch, if it lives in the repository cloned.

    A head with no repository is a fork that has since been deleted, which is
    no more cloneable than a live one.
    """
    if not isinstance(pull_request, dict) or not isinstance(repository, dict):
        return None
    head = pull_request.get("head") or {}
    head_repository = head.get("repo") or {}
    if head_repository.get("id") is None or head_repository.get("id") != repository.get(
        "id"
    ):
        return None
    return head.get("ref")


def _repo_context(payload: WebhookPayload, event: str) -> WebhookPayload:
    """Where the event happened, in the shape a conversation's metadata takes.

    This is the point of the whole path: a triggered agent wakes up already
    inside `octo/api` with the pull request's branch checked out, rather than in
    an empty scratchpad being told about a repository it then has to find.

    `account_id` is filled in later, by the schedule -- the clone runs as the
    *person*, using their `git`/`gh` credentials, and the delivery does not know
    which Lemma account that is.
    """
    repository = payload.get("repository") or {}
    owner = (repository.get("owner") or {}).get("login")
    name = repository.get("name")
    if not owner or not name:
        return {}
    repo: WebhookPayload = {"owner": owner, "repo": name}
    ref = _ref_for(payload, event)
    if ref:
        repo["ref"] = ref
    return {"repo": repo}


def _narrower(
    repository_id: object, action: object
) -> Callable[[WebhookPayload], bool]:
    """The optional per-schedule narrowing, applied after the routing key.

    Both keys are absent from most schedules and both default to accepting, so
    a schedule that says only `{source, installation_id, event}` fires on every
    delivery of that event in that installation -- which is what declaring
    nothing further should mean.
    """

    def accepts(config: WebhookPayload) -> bool:
        wanted_repository = config.get("repository_id")
        if wanted_repository is not None and str(wanted_repository) != str(
            repository_id
        ):
            return False
        wanted_actions = config.get("actions")
        if isinstance(wanted_actions, list) and wanted_actions:
            return action in wanted_actions
        return True

    return accepts


async def _retire_installation(installation_id: str, *, action: str) -> None:
    """Stand down what an uninstalled App leaves behind.

    Each module does its own half: `connectors` owns `accounts` and `schedule`
    owns `schedules`, and neither table is this file's to write. What belongs
    here is only knowing that a GitHub installation going away means both.
    """
    from app.core.api.dependencies import get_uow_factory
    from app.core.authorization.scope import uow_scope
    from app.modules.connectors.contracts.retirement import (
        retire_accounts_for_tenant,
    )
    from app.modules.schedule.contracts.retirement import (
        deactivate_matching_schedules,
    )

    async with uow_scope(get_uow_factory()) as uow:
        accounts = await retire_accounts_for_tenant(
            uow.session, connector_id="github", external_ref=installation_id
        )
        schedules = await deactivate_matching_schedules(
            uow.session,
            criteria={"source": "github", "installation_id": installation_id},
            reason=f"github_installation_{action}",
        )
        await uow.commit()

    logger.warning(
        "schedule.webhook_sources.github.installation_retired.degraded",
        action=action,
        accounts=accounts,
        schedules=schedules,
    )
