#input_type_name: CheckIntegrationsInput
#output_type_name: CheckIntegrationsResult
#function_name: check_integrations

import os
from pydantic import BaseModel
from lemma_sdk import FunctionContext

try:
    import httpx
    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False


class CheckIntegrationsInput(BaseModel):
    apps: list[str] = ["slack", "telegram", "gmail", "googlecalendar", "outlook"]


class CheckIntegrationsResult(BaseModel):
    connected: list[str]
    not_connected: list[str]
    summary: str


async def _get_org_id(base_url: str, pod_id: str, headers: dict) -> str:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{base_url}/pods/{pod_id}", headers=headers)
            org_id = resp.json().get("organization_id", "")
            if not org_id:
                raise RuntimeError("Pod response did not contain organization_id")
            return org_id
    except Exception as e:
        raise RuntimeError(f"Could not resolve org_id from pod: {e}")


async def check_integrations(ctx: FunctionContext, data: CheckIntegrationsInput) -> CheckIntegrationsResult:
    connected: list[str] = []
    not_connected: list[str] = []

    base_url = os.environ.get("LEMMA_BASE_URL", "https://api.lemma.work")
    token = os.environ.get("LEMMA_TOKEN", "")

    if _HAS_HTTPX and token:
        try:
            headers = {"Authorization": f"Bearer {token}"}
            org_id = await _get_org_id(base_url, ctx.pod_id, headers)
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{base_url}/organizations/{org_id}/integrations/accounts",
                    headers=headers,
                    timeout=10.0,
                )
                if resp.status_code == 200:
                    items = resp.json().get("items", [])
                    connected_app_ids = {
                        item["application_id"]
                        for item in items
                        if item.get("status") == "CONNECTED"
                    }
                    for app in data.apps:
                        if app in connected_app_ids:
                            connected.append(app)
                        else:
                            not_connected.append(app)
                else:
                    not_connected = list(data.apps)
        except Exception:
            not_connected = list(data.apps)
    else:
        not_connected = list(data.apps)

    if connected and not_connected:
        summary = f"Connected: {', '.join(connected)}. Not connected: {', '.join(not_connected)}."
    elif connected:
        summary = f"All checked apps connected: {', '.join(connected)}."
    else:
        summary = (
            f"No apps connected yet: {', '.join(not_connected)}. "
            "Ask the user to connect the needed integration before proceeding."
        )

    return CheckIntegrationsResult(
        connected=connected,
        not_connected=not_connected,
        summary=summary,
    )
