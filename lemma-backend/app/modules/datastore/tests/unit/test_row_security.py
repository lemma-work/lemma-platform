"""Who a pod-database session reads as, and what a contact-owned table may be."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.core.authorization.anonymous import build_outsider_context
from app.core.authorization.context import ActorType, Context
from app.modules.datastore.domain.datastore_entities import ColumnSchema
from app.modules.datastore.domain.errors import (
    DatastoreAccessDeniedError,
    DatastoreValidationError,
)
from app.modules.datastore.domain.row_security import (
    NOBODY,
    RowAudience,
    RowPrincipal,
)
from app.modules.datastore.services.authorization import DatastoreAuthorization
from app.modules.datastore.services.contact_owned_tables import (
    refuse_conflicts,
    resolve_contact_columns,
)

pytestmark = pytest.mark.unit

POD = uuid4()
CONTACT = UUID(int=5)


def _member(user_id: UUID | None = None, **extra) -> Context:
    return Context(
        actor_type=ActorType.USER,
        actor_id=str(user_id),
        authorizer=SimpleNamespace(),
        user_id=user_id,
        pod_id=POD,
        **extra,
    )


def _outsider(contact_id: UUID | None) -> Context:
    return build_outsider_context(
        session=SimpleNamespace(),
        pod_id=POD,
        organization_id=None,
        contact_id=contact_id,
        actor_id="visitor:unit",
    )


def test_a_member_reads_as_themselves():
    user = uuid4()
    principal = RowPrincipal.of(_member(user), is_pod_admin=True)
    assert principal.settings() == {
        "app.current_user_id": str(user),
        "app.current_user_is_pod_admin": "true",
        "app.rls_audience": "member",
        "app.current_contact_id": "",
    }


def test_a_contact_reads_as_that_contact_and_no_member():
    principal = RowPrincipal.of(_outsider(CONTACT), is_pod_admin=True)
    assert principal.audience is RowAudience.CONTACT
    assert principal.settings()["app.current_contact_id"] == str(CONTACT)
    assert principal.settings()["app.current_user_id"] == str(NOBODY)
    assert principal.settings()["app.current_user_is_pod_admin"] == "false"


def test_an_anonymous_visitor_reads_as_nobody_at_all():
    settings = RowPrincipal.of(_outsider(None)).settings()
    assert settings["app.rls_audience"] == "outsider"
    assert settings["app.current_contact_id"] == ""
    assert settings["app.current_user_id"] == str(NOBODY)


def test_a_workload_carrying_a_contact_reads_as_that_contact():
    """A contact's function run has grants, and still only that contact's rows."""
    workload = _member(None, contact_id=CONTACT)
    assert RowPrincipal.of(workload) == RowPrincipal.contact(CONTACT)


def test_a_contact_owned_table_is_never_per_user_or_public():
    with pytest.raises(DatastoreValidationError):
        refuse_conflicts(per_user=True, contact_owned=True, visibility="POD")
    with pytest.raises(DatastoreValidationError):
        refuse_conflicts(per_user=False, contact_owned=True, visibility="PUBLIC")
    refuse_conflicts(per_user=False, contact_owned=False, visibility="PUBLIC")


COLUMNS = [ColumnSchema(name=n, type="TEXT") for n in ("subject", "notes")]


def test_the_columns_a_contact_reads_are_chosen_not_defaulted():
    with pytest.raises(DatastoreValidationError):
        resolve_contact_columns(COLUMNS, contact_owned=True, chosen=None, current=[])
    with pytest.raises(DatastoreValidationError):
        resolve_contact_columns(COLUMNS, contact_owned=True, chosen=["x"], current=[])
    with pytest.raises(DatastoreValidationError):
        resolve_contact_columns(
            COLUMNS, contact_owned=False, chosen=["subject"], current=[]
        )
    kept = resolve_contact_columns(
        COLUMNS, contact_owned=True, chosen=None, current=["subject"]
    )
    assert kept == ["subject"]
    assert (
        resolve_contact_columns(
            COLUMNS, contact_owned=False, chosen=None, current=["subject"]
        )
        == []
    )


@pytest.mark.parametrize("write", [False, True])
async def test_an_outsiders_record_tools_never_reach_a_contact_owned_table(
    write, monkeypatch
):
    from app.modules.datastore.services import authorization as module

    monkeypatch.setattr(module, "get_current_context", lambda: _outsider(CONTACT))
    gateway = DatastoreAuthorization(SimpleNamespace())
    table = SimpleNamespace(
        pod_id=POD, table_id=uuid4(), table_name="orders", contact_owned=True
    )
    check = gateway.require_record_write if write else gateway.require_record_read
    with pytest.raises(DatastoreAccessDeniedError):
        await check(user_id=None, ctx=table)
