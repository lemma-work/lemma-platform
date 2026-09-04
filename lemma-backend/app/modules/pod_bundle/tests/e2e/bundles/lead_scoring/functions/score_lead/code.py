# input_type_name: ScoreInput
# output_type_name: ScoreResult
# function_name: score_lead

from typing import Optional

from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod
from lemma_sdk.errors import LemmaAPIError

# Deterministic firmographic scoring so the e2e can assert exact numbers.
_PLAN_BONUS = {"enterprise": 40, "pro": 20}


class ScoreInput(BaseModel):
    company: str
    employees: int = 0
    plan_interest: str = ""


class ScoreResult(BaseModel):
    denied: bool = False
    status_code: Optional[int] = None
    error_code: Optional[str] = None
    lead_id: Optional[str] = None
    score: Optional[int] = None
    tier: Optional[str] = None


def _tier(score: int) -> str:
    if score >= 70:
        return "HIGH"
    if score >= 40:
        return "MEDIUM"
    return "LOW"


async def score_lead(ctx: FunctionContext, data: ScoreInput) -> ScoreResult:
    size_points = min(60, max(0, data.employees) // 5)
    bonus = _PLAN_BONUS.get(data.plan_interest.strip().lower(), 0)
    score = min(100, size_points + bonus)
    tier = _tier(score)
    pod = Pod.from_env()
    try:
        record = pod.table("leads").create(
            {
                "company": data.company,
                "employees": data.employees,
                "plan_interest": data.plan_interest,
                "score": score,
                "tier": tier,
            }
        )
    except LemmaAPIError as exc:
        return ScoreResult(
            denied=True, status_code=exc.status_code, error_code=exc.code
        )
    return ScoreResult(lead_id=str(record["id"]), score=score, tier=tier)
