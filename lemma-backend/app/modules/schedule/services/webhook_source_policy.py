"""Which webhook source a schedule is allowed to name.

`POST /webhooks/{source}` answers only for the sources in the registry -- that
is the allow-list, and its own docstring says absence is a refusal. A schedule
that routes on a source the registry does not know is therefore accepted,
stored, listed as active, and permanently silent. The only moment that can be
said to the person who chose it is the moment they write it.

Only new writes are checked, and on an update only a config the caller wrote
-- retargeting a schedule re-derives the config it already had, and that must
not fail over a source it was created with. A row that already names an
unsupported source keeps existing and keeps not firing: the ingress refuses
that delivery long before matching, so it is inert rather than dangerous, and
failing it on an unrelated edit would turn a dead trigger into a schedule its
owner can no longer rename or switch off.
"""

from __future__ import annotations

from app.modules.schedule.contracts.webhook_source import WebhookSourceRegistry
from app.modules.schedule.domain.errors import ScheduleValidationError
from app.modules.schedule.domain.schedule import ScheduleCreateEntity, ScheduleEntity


def validate_webhook_source(
    schedule: ScheduleCreateEntity | ScheduleEntity,
    sources: WebhookSourceRegistry | None,
) -> None:
    """Refuse a WEBHOOK schedule routed on a source that delivers to nobody.

    Scoped to the schedules whose stored config *is* the whole routing key --
    the ones nothing will provision. A schedule bound to an account and a
    connector trigger is routed by what provisioning writes instead (a provider
    trigger id for Composio, an installation id and event for a GitHub App,
    which also overwrites `source` with its own), and one that cannot be
    provisioned is already refused by the writer with its own message. Checking
    `source` there would refuse working schedules over a word nothing reads.

    A config that names no source at all is left alone for the same reason: an
    unprovisioned schedule may still be matched on any other key its author
    stored, and a bare `{}` is a shape this module has no opinion about.

    `sources` is None only when the service was built without the deployment's
    registry. `get_schedule_service` is the seam that supplies it and every
    creator of a schedule comes through it, so in a running deployment this is
    never None; having nothing to check against is not the same as having found
    something wrong, so that case passes rather than refuses.
    """
    if sources is None:
        return
    if schedule.account_id and schedule.connector_trigger_id:
        return
    named = (schedule.config or {}).get("source")
    if named is None:
        return
    if isinstance(named, str) and sources.for_source(named) is not None:
        return
    accepted = ", ".join(sources.sources)
    raise ScheduleValidationError(
        f"No webhook source named '{named}' delivers to this deployment, so a "
        "schedule listening for it could never fire. Accepted sources: "
        f"{accepted or '(none)'}."
    )
