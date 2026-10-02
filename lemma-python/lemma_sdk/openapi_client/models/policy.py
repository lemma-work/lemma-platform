from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.lane import Lane
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.policy_require_confidence import PolicyRequireConfidence
    from ..models.policy_rules_only import PolicyRulesOnly


T = TypeVar("T", bound="Policy")


@_attrs_define
class Policy:
    """What each rung may answer, and when a rung passes a question on.

    Attributes:
        abstain_below (float | Unset): System One's confidence below which a choice or scale answer is passed on rather
            than taken. Default: 0.6.
        escalate_to_model (bool | Unset): Ask the model when System One abstains. Off, a System One abstention leaves
            the question open. Default: True.
        lane (Lane | Unset): Who is waiting, which decides how long a rung may take.

            Interactive: a person is waiting in a conversation. Ambient: an event is
            being sorted and nobody is watching. Bulk: many rows at once, which yields
            to the other two.
        require_confidence (PolicyRequireConfidence | Unset): Per question and answer, the System One confidence an
            engine answer needs to stand. A rung that reports no confidence never meets it.
        rules_only (PolicyRulesOnly | Unset): Per question, answers only the rules rung may give. An engine that gives
            one has not answered.
        yes_no_band (list[float] | Unset): Probabilities of yes inside this band are passed on rather than taken, for
            yes/no and each option of a multi-choice.
    """

    abstain_below: float | Unset = 0.6
    escalate_to_model: bool | Unset = True
    lane: Lane | Unset = UNSET
    require_confidence: PolicyRequireConfidence | Unset = UNSET
    rules_only: PolicyRulesOnly | Unset = UNSET
    yes_no_band: list[float] | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        abstain_below = self.abstain_below

        escalate_to_model = self.escalate_to_model

        lane: str | Unset = UNSET
        if not isinstance(self.lane, Unset):
            lane = self.lane.value

        require_confidence: dict[str, Any] | Unset = UNSET
        if not isinstance(self.require_confidence, Unset):
            require_confidence = self.require_confidence.to_dict()

        rules_only: dict[str, Any] | Unset = UNSET
        if not isinstance(self.rules_only, Unset):
            rules_only = self.rules_only.to_dict()

        yes_no_band: list[float] | Unset = UNSET
        if not isinstance(self.yes_no_band, Unset):
            yes_no_band = []
            for yes_no_band_item_data in self.yes_no_band:
                yes_no_band_item: float
                yes_no_band_item = yes_no_band_item_data
                yes_no_band.append(yes_no_band_item)

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if abstain_below is not UNSET:
            field_dict["abstain_below"] = abstain_below
        if escalate_to_model is not UNSET:
            field_dict["escalate_to_model"] = escalate_to_model
        if lane is not UNSET:
            field_dict["lane"] = lane
        if require_confidence is not UNSET:
            field_dict["require_confidence"] = require_confidence
        if rules_only is not UNSET:
            field_dict["rules_only"] = rules_only
        if yes_no_band is not UNSET:
            field_dict["yes_no_band"] = yes_no_band

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.policy_require_confidence import PolicyRequireConfidence
        from ..models.policy_rules_only import PolicyRulesOnly

        d = dict(src_dict)
        abstain_below = d.pop("abstain_below", UNSET)

        escalate_to_model = d.pop("escalate_to_model", UNSET)

        _lane = d.pop("lane", UNSET)
        lane: Lane | Unset
        if isinstance(_lane, Unset):
            lane = UNSET
        else:
            lane = Lane(_lane)

        _require_confidence = d.pop("require_confidence", UNSET)
        require_confidence: PolicyRequireConfidence | Unset
        if isinstance(_require_confidence, Unset):
            require_confidence = UNSET
        else:
            require_confidence = PolicyRequireConfidence.from_dict(_require_confidence)

        _rules_only = d.pop("rules_only", UNSET)
        rules_only: PolicyRulesOnly | Unset
        if isinstance(_rules_only, Unset):
            rules_only = UNSET
        else:
            rules_only = PolicyRulesOnly.from_dict(_rules_only)

        _yes_no_band = d.pop("yes_no_band", UNSET)
        yes_no_band: list[float] | Unset = UNSET
        if _yes_no_band is not UNSET:
            yes_no_band = []
            for yes_no_band_item_data in _yes_no_band:

                def _parse_yes_no_band_item(data: object) -> float:
                    return cast(float, data)

                yes_no_band_item = _parse_yes_no_band_item(yes_no_band_item_data)

                yes_no_band.append(yes_no_band_item)

        policy = cls(
            abstain_below=abstain_below,
            escalate_to_model=escalate_to_model,
            lane=lane,
            require_confidence=require_confidence,
            rules_only=rules_only,
            yes_no_band=yes_no_band,
        )

        return policy
