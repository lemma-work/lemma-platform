#input_type_name: SendPingInput
#output_type_name: SendPingResult
#function_name: send_ping

import os
import traceback
import httpx
from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod
from typing import Optional


class SendPingInput(BaseModel):
    person_id: str
    message: str
    channel: str = "auto"           # "slack" | "gmail" | "auto"
    commitment_id: Optional[str] = None
    subject: Optional[str] = None   # Gmail subject; defaults to "Quick check-in"


class SendPingResult(BaseModel):
    sent: bool
    channel_used: str
    ping_id: Optional[str] = None
    error: Optional[str] = None


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


async def send_ping(ctx: FunctionContext, data: SendPingInput) -> SendPingResult:
    pod = Pod.from_env()
    base_url = os.environ.get("LEMMA_BASE_URL", "https://api.lemma.work")
    token    = os.environ.get("LEMMA_TOKEN", "")
    headers  = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # Look up person
    person_raw = pod.table("people").get(data.person_id).to_dict().get("data", {})
    if not person_raw:
        return SendPingResult(sent=False, channel_used="", error="Person not found")

    email = (person_raw.get("email") or "").strip()
    if not email:
        return SendPingResult(sent=False, channel_used="", error="Person has no email — add one first")

    try:
        org_id = await _get_org_id(base_url, ctx.pod_id, headers)
    except RuntimeError as e:
        return SendPingResult(sent=False, channel_used="", error=str(e))

    channels = (
        ["slack"]          if data.channel == "slack" else
        ["gmail"]          if data.channel == "gmail" else
        ["slack", "gmail"]                               # auto: Slack first
    )

    channel_used       = ""
    slack_channel_id:  Optional[str] = None
    slack_thread_ts:   Optional[str] = None
    gmail_thread_id:   Optional[str] = None

    for ch in channels:
        if ch == "slack":
            try:
                lu = await _exec_integration(base_url, org_id, headers,
                                             "slack", "users_lookup_by_email",
                                             {"email": email})
                slack_user_id = (lu.get("user") or {}).get("id")
                if not slack_user_id:
                    continue

                msg = await _exec_integration(base_url, org_id, headers,
                                              "slack", "chat_post_message",
                                              {"channel": slack_user_id, "text": data.message})
                slack_channel_id = msg.get("channel")
                slack_thread_ts  = msg.get("ts")
                channel_used = "slack"
                break
            except Exception as e:
                print(f"Slack send failed: {e}")
                continue

        elif ch == "gmail":
            try:
                subject = data.subject or "Quick check-in"
                result = await _exec_integration(base_url, org_id, headers,
                                                 "gmail", "GMAIL_SEND_EMAIL",
                                                 {
                                                     "recipient_email": email,
                                                     "subject": subject,
                                                     "body": data.message,
                                                 })
                gmail_thread_id = (
                    result.get("threadId")
                    or result.get("thread_id")
                    or result.get("id")
                )
                channel_used = "gmail"
                break
            except Exception as e:
                print(f"Gmail send failed: {e}")
                continue

    if not channel_used:
        return SendPingResult(
            sent=False,
            channel_used="",
            error="Could not send — no connected channel reached this person",
        )

    # Log ping
    ping_data: dict = {
        "person_id": data.person_id,
        "channel":   channel_used,
        "message":   data.message,
    }
    if data.commitment_id:
        ping_data["commitment_id"] = data.commitment_id
    if slack_channel_id:
        ping_data["slack_channel_id"] = slack_channel_id
    if slack_thread_ts:
        ping_data["slack_thread_ts"] = slack_thread_ts
    if gmail_thread_id:
        ping_data["gmail_thread_id"] = gmail_thread_id

    ping = pod.table("pings").create(ping_data).to_dict().get("data", {})
    ping_id = str(ping.get("id", ""))

    return SendPingResult(sent=True, channel_used=channel_used, ping_id=ping_id)
