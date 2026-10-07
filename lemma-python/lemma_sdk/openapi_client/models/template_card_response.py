from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.template_offer_response import TemplateOfferResponse
    from ..models.template_skill_response import TemplateSkillResponse
    from ..models.template_table_response import TemplateTableResponse
    from ..models.template_win_response import TemplateWinResponse


T = TypeVar("T", bound="TemplateCardResponse")


@_attrs_define
class TemplateCardResponse:
    """A role on the hiring shelf, read from the template that makes it.

    Attributes:
        about (str): The template's description; written onto the pod.
        brings (list[str]):
        judged_on (list[str]): The measures its scorecard turns on, beyond the ones every teammate has.
        name (str):
        offers (list[TemplateOfferResponse]):
        role (str): The job, in one line.
        seed (str): The archetype face's seed.
        skills (list[TemplateSkillResponse]):
        tables (list[TemplateTableResponse]):
        template (str): Pass as `template` to a kind=TEMPLATE import.
        wins (list[TemplateWinResponse]):
    """

    about: str
    brings: list[str]
    judged_on: list[str]
    name: str
    offers: list[TemplateOfferResponse]
    role: str
    seed: str
    skills: list[TemplateSkillResponse]
    tables: list[TemplateTableResponse]
    template: str
    wins: list[TemplateWinResponse]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        about = self.about

        brings = self.brings

        judged_on = self.judged_on

        name = self.name

        offers = []
        for offers_item_data in self.offers:
            offers_item = offers_item_data.to_dict()
            offers.append(offers_item)

        role = self.role

        seed = self.seed

        skills = []
        for skills_item_data in self.skills:
            skills_item = skills_item_data.to_dict()
            skills.append(skills_item)

        tables = []
        for tables_item_data in self.tables:
            tables_item = tables_item_data.to_dict()
            tables.append(tables_item)

        template = self.template

        wins = []
        for wins_item_data in self.wins:
            wins_item = wins_item_data.to_dict()
            wins.append(wins_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "about": about,
                "brings": brings,
                "judged_on": judged_on,
                "name": name,
                "offers": offers,
                "role": role,
                "seed": seed,
                "skills": skills,
                "tables": tables,
                "template": template,
                "wins": wins,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.template_offer_response import TemplateOfferResponse
        from ..models.template_skill_response import TemplateSkillResponse
        from ..models.template_table_response import TemplateTableResponse
        from ..models.template_win_response import TemplateWinResponse

        d = dict(src_dict)
        about = d.pop("about")

        brings = cast(list[str], d.pop("brings"))

        judged_on = cast(list[str], d.pop("judged_on"))

        name = d.pop("name")

        offers = []
        _offers = d.pop("offers")
        for offers_item_data in _offers:
            offers_item = TemplateOfferResponse.from_dict(offers_item_data)

            offers.append(offers_item)

        role = d.pop("role")

        seed = d.pop("seed")

        skills = []
        _skills = d.pop("skills")
        for skills_item_data in _skills:
            skills_item = TemplateSkillResponse.from_dict(skills_item_data)

            skills.append(skills_item)

        tables = []
        _tables = d.pop("tables")
        for tables_item_data in _tables:
            tables_item = TemplateTableResponse.from_dict(tables_item_data)

            tables.append(tables_item)

        template = d.pop("template")

        wins = []
        _wins = d.pop("wins")
        for wins_item_data in _wins:
            wins_item = TemplateWinResponse.from_dict(wins_item_data)

            wins.append(wins_item)

        template_card_response = cls(
            about=about,
            brings=brings,
            judged_on=judged_on,
            name=name,
            offers=offers,
            role=role,
            seed=seed,
            skills=skills,
            tables=tables,
            template=template,
            wins=wins,
        )

        template_card_response.additional_properties = d
        return template_card_response

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Any:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
