"""What direct querying answers when the deployment cannot support it.

`PS-DATA-021` promises the facility reports itself unavailable rather than
failing each query as though the person had written a bad one. The state is
real and not exotic: ad-hoc SQL runs as a dedicated Postgres role
(``datastore_query_role``, see ``record_repository._execute_readonly``), and a
managed Postgres that never provisioned it — or an app role without the power
to create it — leaves every query with nowhere to run.

It lives here rather than in the scenario suite because inducing it means
taking the role away, and a scenario cannot: the suite forbids mocking, and
dropping the role from a shared stack breaks every other scenario using it.
This is what the module e2e suite is for.
"""

from __future__ import annotations

import pytest

from app.modules.datastore.config import datastore_settings
from app.modules.datastore.tests.e2e.harness import DatastoreApi

pytestmark = pytest.mark.e2e


class TestQueryingWhenTheRoleIsAbsent:
    @pytest.mark.asyncio
    async def test_a_query_says_the_facility_is_unavailable(
        self,
        pod_api: DatastoreApi,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Unavailable is a fact about the deployment, not about the query.

        A person who wrote perfectly good SQL must not be told their query was
        the problem — they will rewrite it, and rewrite it again. The answer
        has to name the facility.
        """
        monkeypatch.setattr(
            datastore_settings,
            "datastore_query_role",
            "lemma_datastore_query_that_was_never_created",
        )

        response = await pod_api.request(
            "POST",
            f"/pods/{pod_api.pod_id}/datastore/query",
            json={"query": "SELECT 1 AS one"},
        )

        assert response.status_code == 503, (
            f"a query answered {response.status_code} when the query role does "
            f"not exist. 4xx blames the person for SQL that is fine, and 5xx "
            f"without a code is indistinguishable from a crash — neither tells "
            f"an operator the facility needs provisioning: {response.text[:400]}"
        )
        body = response.json()
        assert (
            "unavailable" in str(body.get("code", "")).lower()
            or "unavailable" in str(body.get("message", "")).lower()
        ), f"the refusal does not say the facility is unavailable: {body}"
