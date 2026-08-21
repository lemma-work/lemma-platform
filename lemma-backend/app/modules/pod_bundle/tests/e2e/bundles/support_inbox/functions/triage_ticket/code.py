# input_type_name: TriageInput
# output_type_name: TriageResult
# function_name: triage_ticket

from typing import Optional

from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod
from lemma_sdk.errors import LemmaAPIError

# Words that push an inbound message to the top of the queue. Deterministic so
# the e2e can assert the exact priority without an LLM in the loop.
_URGENT_MARKERS = ("urgent", "refund", "broken", "asap", "immediately", "down")


class TriageInput(BaseModel):
    subject: str
    body: str = ""


class TriageResult(BaseModel):
    denied: bool = False
    status_code: Optional[int] = None
    error_code: Optional[str] = None
    ticket_id: Optional[str] = None
    priority: Optional[str] = None


async def triage_ticket(ctx: FunctionContext, data: TriageInput) -> TriageResult:
    haystack = f"{data.subject}\n{data.body}".lower()
    priority = (
        "HIGH" if any(marker in haystack for marker in _URGENT_MARKERS) else "LOW"
    )
    pod = Pod.from_env()
    try:
        record = pod.table("tickets").create(
            {
                "subject": data.subject,
                "body": data.body,
                "priority": priority,
                "status": "OPEN",
            }
        )
    except LemmaAPIError as exc:
        # Surface an authorization failure as structured output so the test can
        # assert on the real status/code instead of an opaque run failure.
        return TriageResult(
            denied=True, status_code=exc.status_code, error_code=exc.code
        )
    return TriageResult(ticket_id=str(record["id"]), priority=priority)
