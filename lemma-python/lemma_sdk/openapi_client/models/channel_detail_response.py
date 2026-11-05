from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.channel_kind import ChannelKind
from ..models.contact_status import ContactStatus
from ..models.visibility import Visibility
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.attention_response import AttentionResponse
    from ..models.channel_member_response import ChannelMemberResponse
    from ..models.channel_source_response import ChannelSourceResponse
    from ..models.line_preview import LinePreview
    from ..models.my_membership_response import MyMembershipResponse
    from ..models.person_ref import PersonRef
    from ..models.source_settings_response import SourceSettingsResponse
    from ..models.source_summary import SourceSummary
    from ..models.unread_response import UnreadResponse


T = TypeVar("T", bound="ChannelDetailResponse")


@_attrs_define
class ChannelDetailResponse:
    """
    Attributes:
        created_at (datetime.datetime):
        id (UUID):
        kind (ChannelKind):
        last_line_at (datetime.datetime):
        visibility (Visibility):
        agents_answer (bool | None | Unset): A contact's channel: whether its agents answer the contact.
        answers_shared (bool | None | Unset): A group's: whether an answer an agent gives a member here with their own
            access is read by everybody in it (the default), or by that member alone until they share it. Absent for another
            kind.
        archived (bool | Unset):  Default: False.
        assignee (None | PersonRef | Unset): A contact's channel: who answers them by hand.
        attention (AttentionResponse | None | Unset): A contact's channel: its current case asks for a person, and why.
            Never set on another kind of channel.
        case_id (None | Unset | UUID): A contact's channel: its current case, the one `status`, `assignee` and
            `attention` are read from (`/cases` lists them all).
        hand_back_after_hours (int | None | Unset): A contact's channel: how long the assignee holds the contact after
            their last line before the agent answers again.
        joined (bool | Unset): Whether the reader is in it. A pod admin listing a bot's groups (`source_surface`) is
            shown its private ones too, not joined and without their last line, until they join. Default: False.
        last_line (LinePreview | None | Unset):
        last_seq (int | Unset):  Default: 0.
        me (MyMembershipResponse | Unset):
        members (list[ChannelMemberResponse] | Unset):
        opted_out (bool | None | Unset): A contact's channel: whether they asked the pod to stop writing to a handle of
            theirs -- lines to it are not sent until they write from it again (the contact's `identities` say which).
        person (None | PersonRef | Unset): Whose channel it is: the member's for a DIRECT one, the contact's for a
            CONTACT one; absent for a GROUP.
        source_places (list[ChannelSourceResponse] | Unset):
        source_settings (None | SourceSettingsResponse | Unset): Listed by `source_surface`: that bot's settings for its
            place here -- who answers its people outside the pod, and whether they are.
        sources (list[SourceSummary] | Unset): The outside places feeding it.
        status (ContactStatus | None | Unset): A contact's channel: where it stands.
        title (None | str | Unset):
        topic (None | str | Unset):
        unread (UnreadResponse | Unset):
    """

    created_at: datetime.datetime
    id: UUID
    kind: ChannelKind
    last_line_at: datetime.datetime
    visibility: Visibility
    agents_answer: bool | None | Unset = UNSET
    answers_shared: bool | None | Unset = UNSET
    archived: bool | Unset = False
    assignee: None | PersonRef | Unset = UNSET
    attention: AttentionResponse | None | Unset = UNSET
    case_id: None | Unset | UUID = UNSET
    hand_back_after_hours: int | None | Unset = UNSET
    joined: bool | Unset = False
    last_line: LinePreview | None | Unset = UNSET
    last_seq: int | Unset = 0
    me: MyMembershipResponse | Unset = UNSET
    members: list[ChannelMemberResponse] | Unset = UNSET
    opted_out: bool | None | Unset = UNSET
    person: None | PersonRef | Unset = UNSET
    source_places: list[ChannelSourceResponse] | Unset = UNSET
    source_settings: None | SourceSettingsResponse | Unset = UNSET
    sources: list[SourceSummary] | Unset = UNSET
    status: ContactStatus | None | Unset = UNSET
    title: None | str | Unset = UNSET
    topic: None | str | Unset = UNSET
    unread: UnreadResponse | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.attention_response import AttentionResponse
        from ..models.line_preview import LinePreview
        from ..models.person_ref import PersonRef
        from ..models.source_settings_response import SourceSettingsResponse

        created_at = self.created_at.isoformat()

        id = str(self.id)

        kind = self.kind.value

        last_line_at = self.last_line_at.isoformat()

        visibility = self.visibility.value

        agents_answer: bool | None | Unset
        if isinstance(self.agents_answer, Unset):
            agents_answer = UNSET
        else:
            agents_answer = self.agents_answer

        answers_shared: bool | None | Unset
        if isinstance(self.answers_shared, Unset):
            answers_shared = UNSET
        else:
            answers_shared = self.answers_shared

        archived = self.archived

        assignee: dict[str, Any] | None | Unset
        if isinstance(self.assignee, Unset):
            assignee = UNSET
        elif isinstance(self.assignee, PersonRef):
            assignee = self.assignee.to_dict()
        else:
            assignee = self.assignee

        attention: dict[str, Any] | None | Unset
        if isinstance(self.attention, Unset):
            attention = UNSET
        elif isinstance(self.attention, AttentionResponse):
            attention = self.attention.to_dict()
        else:
            attention = self.attention

        case_id: None | str | Unset
        if isinstance(self.case_id, Unset):
            case_id = UNSET
        elif isinstance(self.case_id, UUID):
            case_id = str(self.case_id)
        else:
            case_id = self.case_id

        hand_back_after_hours: int | None | Unset
        if isinstance(self.hand_back_after_hours, Unset):
            hand_back_after_hours = UNSET
        else:
            hand_back_after_hours = self.hand_back_after_hours

        joined = self.joined

        last_line: dict[str, Any] | None | Unset
        if isinstance(self.last_line, Unset):
            last_line = UNSET
        elif isinstance(self.last_line, LinePreview):
            last_line = self.last_line.to_dict()
        else:
            last_line = self.last_line

        last_seq = self.last_seq

        me: dict[str, Any] | Unset = UNSET
        if not isinstance(self.me, Unset):
            me = self.me.to_dict()

        members: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.members, Unset):
            members = []
            for members_item_data in self.members:
                members_item = members_item_data.to_dict()
                members.append(members_item)

        opted_out: bool | None | Unset
        if isinstance(self.opted_out, Unset):
            opted_out = UNSET
        else:
            opted_out = self.opted_out

        person: dict[str, Any] | None | Unset
        if isinstance(self.person, Unset):
            person = UNSET
        elif isinstance(self.person, PersonRef):
            person = self.person.to_dict()
        else:
            person = self.person

        source_places: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.source_places, Unset):
            source_places = []
            for source_places_item_data in self.source_places:
                source_places_item = source_places_item_data.to_dict()
                source_places.append(source_places_item)

        source_settings: dict[str, Any] | None | Unset
        if isinstance(self.source_settings, Unset):
            source_settings = UNSET
        elif isinstance(self.source_settings, SourceSettingsResponse):
            source_settings = self.source_settings.to_dict()
        else:
            source_settings = self.source_settings

        sources: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.sources, Unset):
            sources = []
            for sources_item_data in self.sources:
                sources_item = sources_item_data.to_dict()
                sources.append(sources_item)

        status: None | str | Unset
        if isinstance(self.status, Unset):
            status = UNSET
        elif isinstance(self.status, ContactStatus):
            status = self.status.value
        else:
            status = self.status

        title: None | str | Unset
        if isinstance(self.title, Unset):
            title = UNSET
        else:
            title = self.title

        topic: None | str | Unset
        if isinstance(self.topic, Unset):
            topic = UNSET
        else:
            topic = self.topic

        unread: dict[str, Any] | Unset = UNSET
        if not isinstance(self.unread, Unset):
            unread = self.unread.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "created_at": created_at,
                "id": id,
                "kind": kind,
                "last_line_at": last_line_at,
                "visibility": visibility,
            }
        )
        if agents_answer is not UNSET:
            field_dict["agents_answer"] = agents_answer
        if answers_shared is not UNSET:
            field_dict["answers_shared"] = answers_shared
        if archived is not UNSET:
            field_dict["archived"] = archived
        if assignee is not UNSET:
            field_dict["assignee"] = assignee
        if attention is not UNSET:
            field_dict["attention"] = attention
        if case_id is not UNSET:
            field_dict["case_id"] = case_id
        if hand_back_after_hours is not UNSET:
            field_dict["hand_back_after_hours"] = hand_back_after_hours
        if joined is not UNSET:
            field_dict["joined"] = joined
        if last_line is not UNSET:
            field_dict["last_line"] = last_line
        if last_seq is not UNSET:
            field_dict["last_seq"] = last_seq
        if me is not UNSET:
            field_dict["me"] = me
        if members is not UNSET:
            field_dict["members"] = members
        if opted_out is not UNSET:
            field_dict["opted_out"] = opted_out
        if person is not UNSET:
            field_dict["person"] = person
        if source_places is not UNSET:
            field_dict["source_places"] = source_places
        if source_settings is not UNSET:
            field_dict["source_settings"] = source_settings
        if sources is not UNSET:
            field_dict["sources"] = sources
        if status is not UNSET:
            field_dict["status"] = status
        if title is not UNSET:
            field_dict["title"] = title
        if topic is not UNSET:
            field_dict["topic"] = topic
        if unread is not UNSET:
            field_dict["unread"] = unread

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.attention_response import AttentionResponse
        from ..models.channel_member_response import ChannelMemberResponse
        from ..models.channel_source_response import ChannelSourceResponse
        from ..models.line_preview import LinePreview
        from ..models.my_membership_response import MyMembershipResponse
        from ..models.person_ref import PersonRef
        from ..models.source_settings_response import SourceSettingsResponse
        from ..models.source_summary import SourceSummary
        from ..models.unread_response import UnreadResponse

        d = dict(src_dict)
        created_at = isoparse(d.pop("created_at"))

        id = UUID(d.pop("id"))

        kind = ChannelKind(d.pop("kind"))

        last_line_at = isoparse(d.pop("last_line_at"))

        visibility = Visibility(d.pop("visibility"))

        def _parse_agents_answer(data: object) -> bool | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | None | Unset, data)

        agents_answer = _parse_agents_answer(d.pop("agents_answer", UNSET))

        def _parse_answers_shared(data: object) -> bool | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | None | Unset, data)

        answers_shared = _parse_answers_shared(d.pop("answers_shared", UNSET))

        archived = d.pop("archived", UNSET)

        def _parse_assignee(data: object) -> None | PersonRef | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                assignee_type_0 = PersonRef.from_dict(data)

                return assignee_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | PersonRef | Unset, data)

        assignee = _parse_assignee(d.pop("assignee", UNSET))

        def _parse_attention(data: object) -> AttentionResponse | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                attention_type_0 = AttentionResponse.from_dict(data)

                return attention_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(AttentionResponse | None | Unset, data)

        attention = _parse_attention(d.pop("attention", UNSET))

        def _parse_case_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                case_id_type_0 = UUID(data)

                return case_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        case_id = _parse_case_id(d.pop("case_id", UNSET))

        def _parse_hand_back_after_hours(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        hand_back_after_hours = _parse_hand_back_after_hours(
            d.pop("hand_back_after_hours", UNSET)
        )

        joined = d.pop("joined", UNSET)

        def _parse_last_line(data: object) -> LinePreview | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                last_line_type_0 = LinePreview.from_dict(data)

                return last_line_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(LinePreview | None | Unset, data)

        last_line = _parse_last_line(d.pop("last_line", UNSET))

        last_seq = d.pop("last_seq", UNSET)

        _me = d.pop("me", UNSET)
        me: MyMembershipResponse | Unset
        if isinstance(_me, Unset):
            me = UNSET
        else:
            me = MyMembershipResponse.from_dict(_me)

        _members = d.pop("members", UNSET)
        members: list[ChannelMemberResponse] | Unset = UNSET
        if _members is not UNSET:
            members = []
            for members_item_data in _members:
                members_item = ChannelMemberResponse.from_dict(members_item_data)

                members.append(members_item)

        def _parse_opted_out(data: object) -> bool | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | None | Unset, data)

        opted_out = _parse_opted_out(d.pop("opted_out", UNSET))

        def _parse_person(data: object) -> None | PersonRef | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                person_type_0 = PersonRef.from_dict(data)

                return person_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | PersonRef | Unset, data)

        person = _parse_person(d.pop("person", UNSET))

        _source_places = d.pop("source_places", UNSET)
        source_places: list[ChannelSourceResponse] | Unset = UNSET
        if _source_places is not UNSET:
            source_places = []
            for source_places_item_data in _source_places:
                source_places_item = ChannelSourceResponse.from_dict(
                    source_places_item_data
                )

                source_places.append(source_places_item)

        def _parse_source_settings(
            data: object,
        ) -> None | SourceSettingsResponse | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                source_settings_type_0 = SourceSettingsResponse.from_dict(data)

                return source_settings_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | SourceSettingsResponse | Unset, data)

        source_settings = _parse_source_settings(d.pop("source_settings", UNSET))

        _sources = d.pop("sources", UNSET)
        sources: list[SourceSummary] | Unset = UNSET
        if _sources is not UNSET:
            sources = []
            for sources_item_data in _sources:
                sources_item = SourceSummary.from_dict(sources_item_data)

                sources.append(sources_item)

        def _parse_status(data: object) -> ContactStatus | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                status_type_0 = ContactStatus(data)

                return status_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(ContactStatus | None | Unset, data)

        status = _parse_status(d.pop("status", UNSET))

        def _parse_title(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        title = _parse_title(d.pop("title", UNSET))

        def _parse_topic(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        topic = _parse_topic(d.pop("topic", UNSET))

        _unread = d.pop("unread", UNSET)
        unread: UnreadResponse | Unset
        if isinstance(_unread, Unset):
            unread = UNSET
        else:
            unread = UnreadResponse.from_dict(_unread)

        channel_detail_response = cls(
            created_at=created_at,
            id=id,
            kind=kind,
            last_line_at=last_line_at,
            visibility=visibility,
            agents_answer=agents_answer,
            answers_shared=answers_shared,
            archived=archived,
            assignee=assignee,
            attention=attention,
            case_id=case_id,
            hand_back_after_hours=hand_back_after_hours,
            joined=joined,
            last_line=last_line,
            last_seq=last_seq,
            me=me,
            members=members,
            opted_out=opted_out,
            person=person,
            source_places=source_places,
            source_settings=source_settings,
            sources=sources,
            status=status,
            title=title,
            topic=topic,
            unread=unread,
        )

        channel_detail_response.additional_properties = d
        return channel_detail_response

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
