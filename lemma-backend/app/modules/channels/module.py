"""Channels module registration."""

from app.core.registry import LemmaModule


def _routers():
    from app.modules.channels.api.attachments import router as files
    from app.modules.channels.api.case_controller import router as cases
    from app.modules.channels.api.controller import router as channels
    from app.modules.channels.api.inbox_controller import router as inbox
    from app.modules.channels.api.member_controller import router as members
    from app.modules.channels.api.message_controller import router as lines

    # The inbox and search have fixed segments under the pod, and
    # `/channels/me` is declared before `/channels/{channel_id}`; members'
    # `me` routes before `{kind}:{id}`.
    return [inbox, channels, members, lines, files, cases]


def _register_work() -> None:
    # Every process: a merge of two contacts (API, a web visitor proving an
    # email) writes this through `ContactMerged`.
    import app.modules.channels.events.change_log_work  # noqa: F401
    import app.modules.channels.events.contact_became_member_work  # noqa: F401
    import app.modules.channels.events.contact_merged_work  # noqa: F401
    import app.modules.channels.events.member_joined_work  # noqa: F401
    import app.modules.channels.events.member_left_work  # noqa: F401
    import app.modules.channels.events.push_work  # noqa: F401
    import app.modules.channels.events.sweeps  # noqa: F401


def _live():
    from app.modules.channels.services.live.topics import live_contribution

    return live_contribution()


module = LemmaModule(
    name="channels", routers=_routers, live=_live, register_work=_register_work
)
