# input_type_name: FlagInput
# output_type_name: FlagResult
# function_name: flag_content

from typing import Optional

from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod
from lemma_sdk.errors import LemmaAPIError

# Deterministic banned-term screen so the e2e can assert the exact verdict.
_BANNED = ("spam", "scam", "phishing", "malware")


class FlagInput(BaseModel):
    content: str
    author: str = ""


class FlagResult(BaseModel):
    denied: bool = False
    status_code: Optional[int] = None
    error_code: Optional[str] = None
    submission_id: Optional[str] = None
    status: Optional[str] = None
    reason: Optional[str] = None


async def flag_content(ctx: FunctionContext, data: FlagInput) -> FlagResult:
    lowered = data.content.lower()
    hit = next((term for term in _BANNED if term in lowered), None)
    status = "FLAGGED" if hit else "APPROVED"
    reason = f"contains banned term '{hit}'" if hit else ""
    pod = Pod.from_env()
    try:
        record = pod.table("submissions").create(
            {
                "author": data.author,
                "content": data.content,
                "status": status,
                "reason": reason,
            }
        )
    except LemmaAPIError as exc:
        return FlagResult(denied=True, status_code=exc.status_code, error_code=exc.code)
    return FlagResult(submission_id=str(record["id"]), status=status, reason=reason)
