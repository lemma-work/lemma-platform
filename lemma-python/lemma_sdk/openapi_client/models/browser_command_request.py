from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.history_command import HistoryCommand
    from ..models.navigate_command import NavigateCommand
    from ..models.tab_command import TabCommand
    from ..models.tabs_command import TabsCommand


T = TypeVar("T", bound="BrowserCommandRequest")


@_attrs_define
class BrowserCommandRequest:
    """
    Attributes:
        action (HistoryCommand | NavigateCommand | TabCommand | TabsCommand):
    """

    action: HistoryCommand | NavigateCommand | TabCommand | TabsCommand

    def to_dict(self) -> dict[str, Any]:
        from ..models.history_command import HistoryCommand
        from ..models.navigate_command import NavigateCommand
        from ..models.tabs_command import TabsCommand

        action: dict[str, Any]
        if isinstance(self.action, NavigateCommand):
            action = self.action.to_dict()
        elif isinstance(self.action, HistoryCommand):
            action = self.action.to_dict()
        elif isinstance(self.action, TabsCommand):
            action = self.action.to_dict()
        else:
            action = self.action.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "action": action,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.history_command import HistoryCommand
        from ..models.navigate_command import NavigateCommand
        from ..models.tab_command import TabCommand
        from ..models.tabs_command import TabsCommand

        d = dict(src_dict)

        def _parse_action(
            data: object,
        ) -> HistoryCommand | NavigateCommand | TabCommand | TabsCommand:
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                action_type_0 = NavigateCommand.from_dict(data)

                return action_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                action_type_1 = HistoryCommand.from_dict(data)

                return action_type_1
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                action_type_2 = TabsCommand.from_dict(data)

                return action_type_2
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            if not isinstance(data, dict):
                raise TypeError()
            action_type_3 = TabCommand.from_dict(data)

            return action_type_3

        action = _parse_action(d.pop("action"))

        browser_command_request = cls(
            action=action,
        )

        return browser_command_request
