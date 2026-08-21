from __future__ import annotations

import sys
import warnings
from pathlib import Path

from pydantic.json_schema import PydanticJsonSchemaWarning

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lemma_connectors.core.auth import OAuth2Credentials
from lemma_connectors.jira.client import JiraClient
from lemma_connectors.jira.generated.pydantic_models import WorkflowCompoundCondition


def test_jira_client_uses_base_url_from_credentials():
    client = JiraClient(
        credentials=OAuth2Credentials(
            access_token="token",
            token_type="Bearer",
            base_url="https://api.atlassian.com/ex/jira/cloud-123",
            cloud_id="cloud-123",
        )
    )

    generated_client = client._ensure_generated_client()

    assert str(generated_client.get_httpx_client().base_url).rstrip("/") == (
        "https://api.atlassian.com/ex/jira/cloud-123"
    )


def test_jira_workflow_compound_condition_schema_has_no_discriminator_warning():
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always", PydanticJsonSchemaWarning)
        schema = WorkflowCompoundCondition.model_json_schema()

    assert schema["$ref"] == "#/$defs/WorkflowCompoundCondition"
    assert schema["$defs"]["WorkflowCompoundCondition"]["type"] == "object"
    assert not any("skipped-discriminator" in str(item.message) for item in captured)
