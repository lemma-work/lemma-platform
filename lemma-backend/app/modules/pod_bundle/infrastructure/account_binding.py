"""Is this imported resource wired to an account its importer actually owns?

The bundle stamps every account reference with the ``connector_id`` and
``connector_kind`` it was exported against, and the importer supplies one of
*their own* org's account ids for the ``${..._account}`` variable. These confirm
the supplied account exists in the target org and matches both -- otherwise the
resource is created pointing at a missing or mismatched account and only fails
opaquely the next time it runs.

Extracted from ``applier.py``, which is past the 600-line ceiling the
architecture ratchet sets. Shared rather than surface-specific: the schedule
step and the surface step hold their account to the same rule, which is why this
is one module and not two private methods.
"""

from __future__ import annotations

from uuid import UUID

from app.modules.pod_bundle.domain.errors import PodBundleDomainError


async def validate_account_binding(
    uow,
    *,
    account_id: object,
    expected_connector: object,
    expected_kind: object,
    resource_label: str,
) -> None:
    """Guard against a surface/schedule being wired to the wrong connector
    account on import.

    The bundle stamps every account reference with the ``connector_id`` and
    ``connector_kind`` it was exported against; the importer supplies one of
    *their own* org's account ids for the ``${..._account}`` variable. Here we
    confirm that supplied account actually exists in the target org and matches
    both — otherwise the resource is created pointing at a
    missing/mismatched account and only fails opaquely when it next runs. The
    connector match mirrors the surface account-binding rule
    (``SurfaceAccountBindingResolver``), so an imported surface is held to the
    same contract as a hand-configured one.

    The kind check matters because one connector id can be installed more
    than one way: a bundle exported against a vendored Slack package does
    not work against a Composio Slack account, since the operation names
    differ. Bundles written before kinds carry the old ``LEMMA``/``COMPOSIO``
    vocabulary, which is compared in its own terms rather than rejected.
    """
    if not account_id or not expected_connector:
        return
    try:
        account_uuid = (
            account_id if isinstance(account_id, UUID) else UUID(str(account_id))
        )
    except (ValueError, TypeError) as exc:
        raise PodBundleDomainError(
            f"{resource_label} was given an invalid connector account id "
            f"'{account_id}'.",
            code="POD_BUNDLE_ACCOUNT_INVALID",
        ) from exc

    from app.composition.pod_bundle_resources import get_connector_service

    service = get_connector_service(uow)
    account = await service.account_repository.get(account_uuid)
    if account is None:
        raise PodBundleDomainError(
            f"{resource_label} references a connector account that does not "
            f"exist in this org. Connect a '{expected_connector}' account and "
            "supply its id for this import.",
            code="POD_BUNDLE_ACCOUNT_NOT_FOUND",
        )
    if str(account.connector_id).lower() != str(expected_connector).lower():
        raise PodBundleDomainError(
            f"{resource_label} needs a '{expected_connector}' account, but the "
            f"supplied account is for '{account.connector_id}'. Connect a "
            f"'{expected_connector}' account and re-run the import.",
            code="POD_BUNDLE_ACCOUNT_CONNECTOR_MISMATCH",
        )
    await _validate_account_kind(
        service=service,
        account=account,
        expected_kind=expected_kind,
        expected_connector=expected_connector,
        resource_label=resource_label,
    )


async def _validate_account_kind(
    *,
    service,
    account,
    expected_kind: object,
    expected_connector: object,
    resource_label: str,
) -> None:
    if not expected_kind:
        return
    actual = await service.get_account_kind(account)
    if actual is None:
        return
    wanted = str(expected_kind).lower()
    # Legacy bundles say LEMMA/COMPOSIO. LEMMA covered every non-composio
    # kind, so it is satisfied by any of them rather than by `package`
    # alone -- an MCP install exported before the rename would otherwise be
    # unimportable.
    if wanted in ("lemma", "composio"):
        from app.modules.connectors.domain.connector import kind_to_provider

        if kind_to_provider(actual).value.lower() == wanted:
            return
    elif actual.lower() == wanted:
        return
    raise PodBundleDomainError(
        f"{resource_label} needs a '{expected_connector}' account installed "
        f"as '{expected_kind}', but the supplied account's install is "
        f"'{actual}'. Connect the right one and re-run the import.",
        code="POD_BUNDLE_ACCOUNT_KIND_MISMATCH",
    )
