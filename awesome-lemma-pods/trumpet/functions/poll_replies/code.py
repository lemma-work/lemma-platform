#input_type_name: PollRepliesInput
#output_type_name: PollRepliesResult
#function_name: poll_replies

import os
import httpx
from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod
from datetime import datetime, timezone, timedelta


class PollRepliesInput(BaseModel):
    max_age_days: int = 7


class PollRepliesResult(BaseModel):
    checked: int
    new_replies: int
    errors: int


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


async def _exec_integration(base_url: str, org_id: str, headers: dict,
                             integration: str, operation: str, payload: dict) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{base_url}/organizations/{org_id}/integrations/{integration}/operations/{operation}/execute",
            json={"payload": payload},
            headers=headers,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"[{resp.status_code}] {resp.text[:300]}")
        return resp.json().get("result", {})


async def poll_replies(ctx: FunctionContext, data: PollRepliesInput) -> PollRepliesResult:
    pod = Pod.from_env()
    base_url = os.environ.get("LEMMA_BASE_URL", "https://api.lemma.work")
    token    = os.environ.get("LEMMA_TOKEN", "")
    headers  = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    try:
        org_id = await _get_org_id(base_url, ctx.pod_id, headers)
    except RuntimeError:
        return PollRepliesResult(checked=0, new_replies=0, errors=1)

    cutoff = (datetime.now(timezone.utc) - timedelta(days=data.max_age_days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    pings = pod.records.list(
        "pings",
        filter=["replied_at IS NULL", f"created_at > '{cutoff}'"],
        limit=50,
    ).to_dict().get("items", [])

    existing_replies = pod.records.list("ping_replies", limit=500).to_dict().get("items", [])
    replied_ping_ids: set[str] = {str(r.get("ping_id")) for r in existing_replies}

    checked    = 0
    new_replies = 0
    errors     = 0

    for ping in pings:
        ping_id = str(ping.get("id"))
        channel = ping.get("channel")

        try:
            found: list[dict] = []

            if channel == "slack":
                slack_ch = ping.get("slack_channel_id")
                slack_ts = ping.get("slack_thread_ts")
                if not slack_ch or not slack_ts:
                    continue

                raw = await _exec_integration(base_url, org_id, headers,
                                              "slack", "conversations_replies",
                                              {"channel": slack_ch, "ts": slack_ts})
                for msg in (raw.get("messages") or [])[1:]:
                    body = (msg.get("text") or "").strip()
                    if body:
                        found.append({"body": body, "received_at": msg.get("ts", "")})

            elif channel == "gmail":
                thread_id = ping.get("gmail_thread_id")
                if not thread_id:
                    continue

                raw = await _exec_integration(base_url, org_id, headers,
                                              "gmail", "GMAIL_FETCH_MESSAGE_BY_THREAD_ID",
                                              {"thread_id": thread_id})
                for msg in (raw.get("messages") or [])[1:]:
                    body = (msg.get("snippet") or "").strip()
                    date = msg.get("internalDate") or datetime.now(timezone.utc).isoformat()
                    if body:
                        found.append({"body": body, "received_at": str(date)})

            checked += 1

            if found and ping_id not in replied_ping_ids:
                for r in found:
                    pod.table("ping_replies").create({
                        "ping_id":     ping_id,
                        "body":        r["body"],
                        "received_at": r["received_at"],
                    })
                pod.table("pings").update(ping_id, {
                    "replied_at": datetime.now(timezone.utc).isoformat()
                })
                new_replies += len(found)

        except Exception as e:
            print(f"Error polling ping {ping_id}: {e}")
            errors += 1

    return PollRepliesResult(checked=checked, new_replies=new_replies, errors=errors)
