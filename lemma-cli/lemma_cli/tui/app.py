"""Lemma TUI connector shell: scope management, pickers, screen routing."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from textual import work
from textual.app import App, SystemCommand
from textual.binding import Binding
from textual.screen import Screen

from .screens.chat import ChatScreen
from .screens.pickers import OrgPickerScreen, PodPickerScreen, ServerPickerScreen
from .screens.pod import PodScreen
from .state import (
    build_tui_state,
    list_orgs,
    list_pods,
    list_servers,
    resolve_org_id,
    resolve_pod_id,
    select_org,
    select_pod,
    switch_server,
)


class LemmaTuiApp(App[None]):
    TITLE = "Lemma"
    CSS_PATH = "styles.tcss"

    # Priority bindings fire before any focused widget (e.g. the chat Input),
    # so Ctrl+C / Ctrl+Q always quit no matter where focus is.
    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit", priority=True),
        Binding("ctrl+q", "quit", "Quit", priority=True, show=False),
    ]

    def __init__(
        self,
        *,
        pod: str | None = None,
        agent: str | None = None,
        config_file: Path | None = None,
    ) -> None:
        super().__init__()
        self.agent = agent
        self.state = build_tui_state(
            config_file=(config_file or Path("~/.lemma/config.json").expanduser()),
            pod=pod,
        )

    # ------------------------------------------------------------ scope

    @property
    def current_org_id(self) -> str | None:
        return resolve_org_id(self.state)

    @property
    def current_pod_id(self) -> str | None:
        return resolve_pod_id(self.state)

    def on_mount(self) -> None:
        self.push_screen(PodScreen())
        if self.current_org_id is None:
            self.pick_org()
        elif self.current_pod_id is None:
            self.pick_pod()

    async def action_quit(self) -> None:
        # Cancel in-flight thread workers (e.g. a blocking chat stream) so the
        # app closes promptly instead of hanging on a pending read.
        self.workers.cancel_all()
        self.exit()

    def get_system_commands(self, screen: Screen):  # type: ignore[no-untyped-def]
        yield from super().get_system_commands(screen)
        yield SystemCommand(
            "Switch server", "Change the active Lemma server", self.pick_server
        )
        yield SystemCommand(
            "Switch organization", "Change the selected org", self.pick_org
        )
        yield SystemCommand("Switch pod", "Change the selected pod", self.pick_pod)
        yield SystemCommand("Open chat", "Chat with the default agent", self.open_chat)

    def refresh_pod_screen(self) -> None:
        for screen in self.screen_stack:
            if isinstance(screen, PodScreen):
                screen.refresh_status()
                screen.reload()

    # ----------------------------------------------------------- pickers

    def pick_server(self) -> None:
        items = list_servers(self.state)

        def done(name: str | None) -> None:
            if not name:
                return
            try:
                switch_server(self.state, name)
            except Exception as exc:
                self.notify(str(exc), severity="error")
                return
            self.notify(f"Server: {name}")
            self.refresh_pod_screen()
            if self.current_org_id is None:
                self.pick_org()
            elif self.current_pod_id is None:
                self.pick_pod()

        self.push_screen(ServerPickerScreen(items), done)

    def pick_org(self) -> None:
        self._load_and_pick_org()

    def pick_pod(self) -> None:
        self._load_and_pick_pod()

    @work(thread=True, exclusive=True, group="pickers")
    def _load_and_pick_org(self) -> None:
        try:
            items = list_orgs(self.state)
        except Exception as exc:
            self.call_from_thread(
                self.notify, f"Could not load orgs: {exc}", severity="error"
            )
            return
        self.call_from_thread(self._push_org_picker, items)

    def _push_org_picker(self, items: list[dict[str, Any]]) -> None:
        for item in items:
            item["active"] = str(item.get("id")) == self.current_org_id

        def done(org_id: str | None) -> None:
            if not org_id:
                return
            self._apply_org(org_id)

        self.push_screen(OrgPickerScreen(items), done)

    @work(thread=True, exclusive=True, group="pickers")
    def _apply_org(self, org_id: str) -> None:
        try:
            org = select_org(self.state, org_id)
        except Exception as exc:
            self.call_from_thread(
                self.notify, f"Org switch failed: {exc}", severity="error"
            )
            return

        def after() -> None:
            self.notify(f"Org: {org.get('name') or org_id}")
            self.refresh_pod_screen()
            self.pick_pod()

        self.call_from_thread(after)

    @work(thread=True, exclusive=True, group="pickers")
    def _load_and_pick_pod(self) -> None:
        try:
            items = list_pods(self.state)
        except Exception as exc:
            self.call_from_thread(
                self.notify, f"Could not load pods: {exc}", severity="error"
            )
            return
        self.call_from_thread(self._push_pod_picker, items)

    def _push_pod_picker(self, items: list[dict[str, Any]]) -> None:
        if not items:
            self.notify("No pods in this organization.", severity="warning")
            return
        for item in items:
            item["active"] = str(item.get("id")) == self.current_pod_id

        def done(pod_id: str | None) -> None:
            if not pod_id:
                return
            self._apply_pod(pod_id)

        self.push_screen(PodPickerScreen(items), done)

    @work(thread=True, exclusive=True, group="pickers")
    def _apply_pod(self, pod_id: str) -> None:
        try:
            pod = select_pod(self.state, pod_id)
        except Exception as exc:
            self.call_from_thread(
                self.notify, f"Pod switch failed: {exc}", severity="error"
            )
            return

        def after() -> None:
            self.notify(f"Pod: {pod.get('name') or pod_id}")
            self.refresh_pod_screen()

        self.call_from_thread(after)

    # -------------------------------------------------------------- chat

    def open_chat(
        self,
        *,
        agent: str | None = None,
        conversation_id: str | None = None,
        title: str | None = None,
    ) -> None:
        if self.current_pod_id is None:
            self.notify("Select a pod first (press 'p').", severity="warning")
            return
        self.push_screen(
            ChatScreen(
                state=self.state,
                agent=agent or self.agent,
                conversation_id=conversation_id,
                title=title,
            )
        )
