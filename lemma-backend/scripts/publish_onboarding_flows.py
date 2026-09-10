"""Publish the two static signup Flows using the configured WhatsApp business account.

Run from lemma-backend: uv run python scripts/publish_onboarding_flows.py.
The printed IDs configure the receiver; access tokens are never printed.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict


class Flow(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    name: str = ""
    status: str = "DRAFT"


class Flows(BaseModel):
    data: list[Flow]


def publish() -> None:
    token = os.environ["WHATSAPP_ACCESS_TOKEN"]
    waba = os.environ["WHATSAPP_WABA_ID"]
    root = Path(__file__).resolve().parents[1] / "manifests" / "whatsapp"
    with httpx.Client(
        base_url="https://graph.facebook.com/v21.0/",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    ) as client:
        response = client.get(
            f"{waba}/flows", params={"fields": "id,name,status", "limit": 100}
        )
        response.raise_for_status()
        existing = Flows.model_validate(response.json()).data
        for step in ("email", "code"):
            name = f"lemma_onboarding_{step}_v1"
            flow = next((item for item in existing if item.name == name), None)
            if flow is None:
                response = client.post(
                    f"{waba}/flows", data={"name": name, "categories": '["SIGN_UP"]'}
                )
                response.raise_for_status()
                flow = Flow.model_validate(response.json())
            if flow.status != "PUBLISHED":
                with (root / f"onboarding-{step}.json").open("rb") as source:
                    response = client.post(
                        f"{flow.id}/assets",
                        data={"name": "flow.json", "asset_type": "FLOW_JSON"},
                        files={"file": ("flow.json", source, "application/json")},
                    )
                response.raise_for_status()
                if response.json().get("validation_errors"):
                    raise RuntimeError(f"Meta rejected the {step} Flow schema")
                response = client.post(f"{flow.id}/publish")
                response.raise_for_status()
            print(f"WHATSAPP_ONBOARDING_{step.upper()}_FLOW_ID={flow.id}")


if __name__ == "__main__":
    publish()
