"""Decisions and deciders: closed-set judgements, asked from a function or an app.

A decision asks a decider -- a pod decider by name, ``system:<name>``, or
questions passed inline -- about one piece of state, and answers from a fixed
set: which of these, yes or no, how much. The first model capability a function
can use without starting an agent. See ``docs/design/decisions.md``.
"""

from __future__ import annotations

import datetime
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from ..openapi_client.api.decisions import (
    decider_create,
    decider_delete,
    decider_get,
    decider_list,
    decider_test,
    decider_update,
    decider_version_list,
    decision_answer,
    decision_create,
    decision_get,
    decision_list,
    decision_rows,
)
from ..openapi_client.models.answer_body import AnswerBody
from ..openapi_client.models.create_decider_body import CreateDeciderBody
from ..openapi_client.models.decide_body import DecideBody
from ..openapi_client.models.decide_rows_body import DecideRowsBody
from ..openapi_client.models.decider_list_response import DeciderListResponse
from ..openapi_client.models.decider_response import DeciderResponse
from ..openapi_client.models.decider_test_body import DeciderTestBody
from ..openapi_client.models.decider_test_response import DeciderTestResponse
from ..openapi_client.models.decider_version_list_response import (
    DeciderVersionListResponse,
)
from ..openapi_client.models.decision_list_response import DecisionListResponse
from ..openapi_client.models.decision_response import DecisionResponse
from ..openapi_client.models.rows_response import RowsResponse
from ..openapi_client.models.update_decider_body import UpdateDeciderBody
from ..openapi_client.types import UNSET
from .base import BoundResource, as_uuid, compact


def _ask(
    *,
    decider: str | None,
    definition: Mapping[str, Any] | None,
    options: Mapping[str, Mapping[str, Any]] | None,
    record: bool,
    visibility: str,
) -> dict[str, Any]:
    if (decider is None) == (definition is None):
        raise ValueError("Pass a decider name or an inline definition, not both.")
    return compact(
        {
            "decider": decider,
            "definition": dict(definition) if definition is not None else None,
            "options": {key: dict(value) for key, value in (options or {}).items()},
            "record": record,
            "visibility": visibility,
        }
    )


class PodDecisions(BoundResource):
    """Asking decisions in this pod, reading them, and answering them."""

    def decide(
        self,
        state: Any,
        *,
        decider: str | None = None,
        definition: Mapping[str, Any] | None = None,
        subject: str | None = None,
        options: Mapping[str, Mapping[str, Any]] | None = None,
        visibility: str = "PERSONAL",
        record: bool = True,
    ) -> DecisionResponse:
        """Ask one decision about ``state``.

        With a ``subject``, the decider is asked about it once: asking again
        returns the recorded decision. ``visibility`` is ``PERSONAL`` unless the
        state is the pod's to share, because the decision keeps it as evidence.
        """
        body = _ask(
            decider=decider,
            definition=definition,
            options=options,
            record=record,
            visibility=visibility,
        )
        body["state"] = state
        if subject is not None:
            body["subject"] = subject
        return self._call(
            decision_create, self._pod_uuid(), body=body, body_model=DecideBody
        )

    def decide_rows(
        self,
        rows: Sequence[Any],
        *,
        decider: str | None = None,
        definition: Mapping[str, Any] | None = None,
        id_field: str | None = None,
        subject_prefix: str | None = None,
        options: Mapping[str, Mapping[str, Any]] | None = None,
        visibility: str = "PERSONAL",
        record: bool = True,
    ) -> RowsResponse:
        """Ask the same decider about many rows at once.

        ``subject_prefix`` with ``id_field`` makes each row's decision asked
        once, as ``<prefix>:<row id>``.
        """
        body = _ask(
            decider=decider,
            definition=definition,
            options=options,
            record=record,
            visibility=visibility,
        )
        body.update(
            compact(
                {
                    "rows": list(rows),
                    "id_field": id_field,
                    "subject_prefix": subject_prefix,
                }
            )
        )
        return self._call(
            decision_rows, self._pod_uuid(), body=body, body_model=DecideRowsBody
        )

    def list(
        self,
        *,
        decider: str | None = None,
        open_only: bool = False,
        before: datetime.datetime | None = None,
        limit: int = 50,
    ) -> DecisionListResponse:
        """Newest first: the pod's shared decisions and your own."""
        return self._call(
            decision_list,
            self._pod_uuid(),
            decider=decider if decider is not None else UNSET,
            open_only=open_only,
            before=before if before is not None else UNSET,
            limit=limit,
        )

    def get(self, decision_id: str | UUID) -> DecisionResponse:
        return self._call(decision_get, self._pod_uuid(), as_uuid(decision_id))

    def answer(
        self, decision_id: str | UUID, answers: Mapping[str, Any]
    ) -> DecisionResponse:
        """Answer an open question, or correct a machine's answer.

        Your answer becomes an example the decider learns from.
        """
        return self._call(
            decision_answer,
            self._pod_uuid(),
            as_uuid(decision_id),
            body={"answers": dict(answers), "by": "person"},
            body_model=AnswerBody,
        )


class PodDeciders(BoundResource):
    """Defining, versioning and testing this pod's deciders."""

    def create(self, name: str, definition: Mapping[str, Any]) -> DeciderResponse:
        return self._call(
            decider_create,
            self._pod_uuid(),
            body={"name": name, "definition": dict(definition)},
            body_model=CreateDeciderBody,
        )

    def list(self, *, limit: int = 100) -> DeciderListResponse:
        return self._call(decider_list, self._pod_uuid(), limit=limit)

    def get(self, name: str) -> DeciderResponse:
        return self._call(decider_get, self._pod_uuid(), name)

    def update(self, name: str, definition: Mapping[str, Any]) -> DeciderResponse:
        """Save a new version. The old one is kept, and decisions name theirs."""
        return self._call(
            decider_update,
            self._pod_uuid(),
            name,
            body={"definition": dict(definition)},
            body_model=UpdateDeciderBody,
        )

    def delete(self, name: str) -> None:
        self._call(decider_delete, self._pod_uuid(), name)

    def versions(self, name: str, *, limit: int = 50) -> DeciderVersionListResponse:
        return self._call(decider_version_list, self._pod_uuid(), name, limit=limit)

    def test(
        self,
        rows: Sequence[Mapping[str, Any]],
        *,
        decider: str | None = None,
        definition: Mapping[str, Any] | None = None,
    ) -> DeciderTestResponse:
        """Decide sample rows without recording anything.

        Each row is ``{"state": ..., "expected": {...}}``; rows with expected
        answers are compared with what the decider said.
        """
        body = _ask(
            decider=decider,
            definition=definition,
            options=None,
            record=False,
            visibility="PERSONAL",
        )
        body.pop("record", None)
        body.pop("visibility", None)
        body["rows"] = [dict(row) for row in rows]
        return self._call(
            decider_test, self._pod_uuid(), body=body, body_model=DeciderTestBody
        )
