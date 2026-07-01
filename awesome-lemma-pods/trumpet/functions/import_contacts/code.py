#input_type_name: ImportContactsInput
#output_type_name: ImportContactsResult
#function_name: import_contacts

import os
import traceback
from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod
from typing import Optional

try:
    import httpx
    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False


class ImportContact(BaseModel):
    name: str
    email: str
    organization: Optional[str] = None


class ImportContactsInput(BaseModel):
    source: str = "gmail"  # "gmail" | "slack" | "both"


class ImportContactsResult(BaseModel):
    contacts: list[ImportContact]
    count: int
    skipped: int
    errors: list[str] = []


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


async def import_contacts(ctx: FunctionContext, data: ImportContactsInput) -> ImportContactsResult:
    pod = Pod.from_env()
    base_url = os.environ.get("LEMMA_BASE_URL", "https://api.lemma.work")
    token    = os.environ.get("LEMMA_TOKEN", "")
    errors: list[str] = []

    if not _HAS_HTTPX or not token:
        return ImportContactsResult(contacts=[], count=0, skipped=0, errors=["httpx or token unavailable"])

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    try:
        org_id = await _get_org_id(base_url, ctx.pod_id, headers)
    except RuntimeError as e:
        return ImportContactsResult(contacts=[], count=0, skipped=0, errors=[str(e)])

    print(f"org_id: {org_id}")

    # Collect existing emails to deduplicate
    existing_rows = pod.records.list("people", limit=500).to_dict().get("items", [])
    existing_emails: set[str] = {
        (r.get("email") or "").lower().strip()
        for r in existing_rows
        if r.get("email")
    }
    print(f"Existing emails: {len(existing_emails)}")

    new_contacts: list[ImportContact] = []
    seen_emails: set[str] = set()
    skipped = 0

    # ── Gmail contacts ─────────────────────────────────────────────────────────
    if data.source in ("gmail", "both"):
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                page_token: Optional[str] = None
                total_fetched = 0
                for page_num in range(3):
                    payload: dict = {"page_size": 100}
                    if page_token:
                        payload["page_token"] = page_token

                    resp = await client.post(
                        f"{base_url}/organizations/{org_id}/integrations/gmail/operations/GMAIL_GET_CONTACTS/execute",
                        json={"payload": payload},
                        headers=headers,
                    )
                    if resp.status_code != 200:
                        errors.append(f"Gmail page {page_num+1} error {resp.status_code}: {resp.text[:200]}")
                        break

                    raw = resp.json().get("result", {})
                    connections = raw.get("connections", [])
                    total_fetched += len(connections)
                    print(f"Gmail page {page_num+1}: {len(connections)} connections")

                    for c in connections:
                        emails_field = c.get("emailAddresses", [])
                        if not emails_field:
                            continue
                        email = (emails_field[0].get("value") or "").lower().strip()
                        if not email:
                            continue

                        names_field = c.get("names", [])
                        name = (names_field[0].get("displayName") or "").strip() if names_field else ""
                        if not name:
                            name = email.split("@")[0]

                        orgs_field = c.get("organizations", [])
                        org = (orgs_field[0].get("name") or "").strip() if orgs_field else None

                        if email in existing_emails or email in seen_emails:
                            skipped += 1
                            continue

                        seen_emails.add(email)
                        new_contacts.append(ImportContact(name=name, email=email, organization=org or None))

                    page_token = raw.get("nextPageToken")
                    if not page_token:
                        break

                print(f"Gmail done: {total_fetched} fetched, {len(new_contacts)} new, {skipped} skipped")
        except Exception:
            errors.append(f"Gmail error: {traceback.format_exc()[:400]}")

    # ── Slack users ────────────────────────────────────────────────────────────
    if data.source in ("slack", "both"):
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                cursor: Optional[str] = None
                slack_start = len(new_contacts)
                for _ in range(5):
                    payload = {"limit": 200}
                    if cursor:
                        payload["cursor"] = cursor

                    resp = await client.post(
                        f"{base_url}/organizations/{org_id}/integrations/slack/operations/users_list/execute",
                        json={"payload": payload},
                        headers=headers,
                    )
                    if resp.status_code != 200:
                        errors.append(f"Slack error {resp.status_code}: {resp.text[:200]}")
                        break

                    raw = resp.json().get("result", {})
                    members = raw.get("members", [])
                    print(f"Slack batch: {len(members)} members")

                    for m in members:
                        if m.get("is_bot") or m.get("deleted") or m.get("id") == "USLACKBOT":
                            continue
                        profile = m.get("profile") or {}
                        email = (profile.get("email") or "").lower().strip()
                        if not email:
                            continue

                        name = (m.get("real_name") or profile.get("real_name") or "").strip()
                        if not name:
                            name = email.split("@")[0]

                        if email in existing_emails or email in seen_emails:
                            skipped += 1
                            continue

                        seen_emails.add(email)
                        new_contacts.append(ImportContact(name=name, email=email))

                    cursor = (raw.get("response_metadata") or {}).get("next_cursor")
                    if not cursor:
                        break
                print(f"Slack done: {len(new_contacts) - slack_start} new")
        except Exception:
            errors.append(f"Slack error: {traceback.format_exc()[:400]}")

    print(f"Final: {len(new_contacts)} new contacts, {skipped} skipped")
    return ImportContactsResult(
        contacts=new_contacts,
        count=len(new_contacts),
        skipped=skipped,
        errors=errors,
    )
