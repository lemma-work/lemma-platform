from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.triage_config_routes import TriageConfigRoutes
    from ..models.triage_digest import TriageDigest


T = TypeVar("T", bound="TriageConfig")


@_attrs_define
class TriageConfig:
    """A decider, and what each of its answers does with the event.

    Attributes:
        decider (str): The pod decider asked about each event.
        routes (TriageConfigRoutes): Every declared option of the question, mapped to act, digest, ask or ignore.
        act_per_hour (int | None | Unset): At most this many act runs an hour. Past it, act becomes digest when the
            schedule has one, and ask when it does not.
        digest (None | TriageDigest | Unset): Required when any option routes to digest.
        question (None | str | Unset): The decider's choice question to route on. Omitted means its only question; saved
            as the one it resolved to.
    """

    decider: str
    routes: TriageConfigRoutes
    act_per_hour: int | None | Unset = UNSET
    digest: None | TriageDigest | Unset = UNSET
    question: None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.triage_digest import TriageDigest

        decider = self.decider

        routes = self.routes.to_dict()

        act_per_hour: int | None | Unset
        if isinstance(self.act_per_hour, Unset):
            act_per_hour = UNSET
        else:
            act_per_hour = self.act_per_hour

        digest: dict[str, Any] | None | Unset
        if isinstance(self.digest, Unset):
            digest = UNSET
        elif isinstance(self.digest, TriageDigest):
            digest = self.digest.to_dict()
        else:
            digest = self.digest

        question: None | str | Unset
        if isinstance(self.question, Unset):
            question = UNSET
        else:
            question = self.question

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "decider": decider,
                "routes": routes,
            }
        )
        if act_per_hour is not UNSET:
            field_dict["act_per_hour"] = act_per_hour
        if digest is not UNSET:
            field_dict["digest"] = digest
        if question is not UNSET:
            field_dict["question"] = question

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.triage_config_routes import TriageConfigRoutes
        from ..models.triage_digest import TriageDigest

        d = dict(src_dict)
        decider = d.pop("decider")

        routes = TriageConfigRoutes.from_dict(d.pop("routes"))

        def _parse_act_per_hour(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        act_per_hour = _parse_act_per_hour(d.pop("act_per_hour", UNSET))

        def _parse_digest(data: object) -> None | TriageDigest | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                digest_type_0 = TriageDigest.from_dict(data)

                return digest_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | TriageDigest | Unset, data)

        digest = _parse_digest(d.pop("digest", UNSET))

        def _parse_question(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        question = _parse_question(d.pop("question", UNSET))

        triage_config = cls(
            decider=decider,
            routes=routes,
            act_per_hour=act_per_hour,
            digest=digest,
            question=question,
        )

        return triage_config
