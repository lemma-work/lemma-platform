"""Per-platform capability registry — the single source of truth for what a
surface platform can do.

These are facts, not prose. The agent module reads them through
``contracts.platforms.platform_facts`` and decides for itself what to tell the
model about them; this registry also drives the delivery branch in the
``display_resource`` tool (chat vs email, native form vs link, native file vs
link) and the progress observer.

Byte caps are reused from :mod:`attachment_limits` rather than duplicated.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.modules.agent_surfaces.platforms.attachment_limits import (
    MediaKind,
    attachment_cap,
    email_inline_cap,
    inline_cap,
    media_cap_summary,
)


class DeliveryCardinality(StrEnum):
    """How many things a run may put in front of the person.

    The fact ``is_email`` kept being asked to stand for. Email delivers one
    composed reply, so everything a run wants to say -- narration, a file, a
    question it cannot pause on -- has to become part of that one reply rather
    than a message of its own. Chat has no such limit.

    It is a property of the platform, not a second code path: the delivery
    reads it and either sends each envelope as it comes or accumulates them and
    flushes once.
    """

    #: Each envelope is delivered when it is ready (every chat platform).
    MANY = "MANY"
    #: Envelopes accumulate into one, flushed at the end of the run (email).
    ONE = "ONE"


class ProgressStyle(StrEnum):
    """How a platform can show that a long run is still going.

    The three sets the progress observer used to hand-maintain — who streams
    tokens, who edits a live message, who gets nothing — were three answers to
    one question, kept in three places that had to be updated together. One field
    on the platform, and the observer reads it.
    """

    #: A real streaming API: tokens append to a message that is closed *with*
    #: the final answer, so the steps and the answer are one message (Slack).
    STREAM = "stream"
    #: One live message, edited in place as the work proceeds, replaced or
    #: cleared at the end (Telegram, Teams).
    EDIT = "edit"
    #: No edit API at all. Progress can only be a *new* message, so it has to be
    #: rare and worth the interruption — a plan, or a single "still going"
    #: (WhatsApp).
    POST = "post"
    #: One composed reply and nothing before it (email).
    NONE = "none"


@dataclass(frozen=True)
class PlatformCapabilities:
    """Stable, per-conversation facts about a surface platform.

    These never change mid-conversation (a conversation never switches platform),
    so anything the agent derives from them is safe to place in the cached
    system-prompt prefix.
    """

    platform: str  # canonical upper key, e.g. "SLACK"
    display_name: str  # human label, e.g. "Slack", "Microsoft Teams"
    supports_native_choices: bool  # native tappable ask_user choices (blocks / cards / inline keyboards / interactive lists)
    supports_native_files: bool  # native file attachment via display_resource type=FILE
    is_email: (
        bool  # one composed reply per run, sent by the observer — no chat delivery
    )
    is_channel_capable: bool  # can be @-mentioned in a multi-party channel
    markdown_mode: (
        str  # mrkdwn|limited_markdown|markdownv2_converted|whatsapp|html_rendered
    )
    formatting_style: str  # one-line human guidance, quoted to the agent verbatim
    soft_char_limit: int  # rough per-message length budget quoted to the agent
    # Can the pod address someone who has never written to us first? Data, not a
    # rule in prose that every new call site has to remember. Chat bots cannot:
    # a Slack/Telegram/WhatsApp bot needs a prior interaction before it may DM.
    # Email genuinely can — it is the only reason an unreachable colleague still
    # gets told anything.
    # Can the agent read the *surrounding* conversation there, or only what the
    # platform hands it? Two facts, and conflating them put a promise in the
    # prompt that one platform cannot keep: Telegram is mention-capable and its
    # `fetch_thread_context` returns at most the single message this one replies
    # to, delivered inline in the update -- its own comment says "Telegram bots
    # cannot read group history". There is no recent-channel-message tool to
    # offer it. Slack and Teams genuinely fetch a window.
    reads_channel_history: bool = False
    can_cold_open: bool = False
    # How this platform can show that a long run is still going. See
    # ``ProgressStyle`` — the observer branches on this instead of on three
    # hand-maintained platform sets.
    progress_style: ProgressStyle = ProgressStyle.NONE
    # Does the progress update land somewhere that holds only one line?
    #
    # Telegram's is a ``tg-thinking`` chip, and its HTML collapses newlines the
    # way a browser does: a five-line checklist arrives as one run-on sentence
    # with the ✅/⏳/⬜ marks stranded mid-paragraph, dimmed to the point of
    # looking like glyphs the font is missing. The fix is not per-platform
    # escaping — the text is already plain — it is sending one line where one
    # line is what will be shown.
    progress_is_one_line: bool = False
    # Hours after the person's last inbound message during which free-form
    # replies are allowed. WhatsApp's 24h customer-service rule is real: past it
    # a send is refused unless it is a pre-approved template. None means no
    # window (every other platform).
    reply_window_hours: int | None = None
    # Is the deployment's system credential for this platform an *identity*, or
    # a shared service key?
    #
    # For every chat platform it is an identity: one Slack app, one Telegram
    # bot, one WhatsApp number. Inbound arrives keyed on that identity and
    # nothing else, so two pods claiming it would misroute each other's
    # messages — which is what `ensure_unique_org_credential_binding` refuses.
    #
    # Resend is the opposite. The credential is an API key over a catch-all
    # domain, and inbound routes on the surface's own `surface_identity_email`,
    # which carries a unique index. Every pod and every agent getting its own
    # address off one key *is* the design, so applying the identity rule here
    # let the first mailbox in an organization block every one after it.
    system_credential_is_identity: bool = True
    # Can a surface here end up on the system credential with *no* identity of
    # its own?
    #
    # Only meaningful where the field above is False, and it is the whole
    # difference between the two platforms that answer False. Resend mints an
    # address for every surface it creates, so one always exists and the
    # exemption is unconditional. A WhatsApp surface is given a number only when
    # the deployment owns a pool to draw from; with no pool it sits on the one
    # number in settings, which is the deployment-wide identity the coarse rule
    # was written for in the first place.
    #
    # Exempting it anyway left nothing constraining it at all: the replacement
    # index `uq_agent_org_whatsapp_number` is partial on `surface_identity_id IS
    # NOT NULL`, so it does not see a surface holding no number either, and two
    # pods in one organization could both take the shared line.
    system_identity_may_be_absent: bool = False
    # Does an inbound platform-wide webhook here arrive on Lemma's own shared bot
    # (one Telegram bot, one WhatsApp number for the deployment)?
    #
    # Only where that is true may a shared webhook be narrowed to the
    # system-credential surfaces. Applying it to every platform would delete the
    # Slack own-app path, where an org signs with its own secret and its surface
    # is legitimately not on system credentials.
    has_shared_system_bot: bool = False
    # Does ``say`` land as a real voice-note bubble? Only where the adapter
    # implements ``_render_voice`` (Telegram's sendVoice). Everywhere else the
    # same audio is delivered as an ordinary attachment, or as a link where the
    # platform cannot receive one -- see ``PlatformEnvelopeDelivery._deliver_voice``.
    supports_native_voice: bool = False
    # Is every group this bot is in one it created? True only for WhatsApp,
    # whose business numbers cannot be added to a group, only create one (see
    # ``services/whatsapp_groups``). Such a group belongs to the pod that asked
    # for it, so its messages go to that pod's surface and no other, whichever
    # pods on the shared number the sender is in.
    bot_creates_groups: bool = False

    @property
    def delivery_cardinality(self) -> DeliveryCardinality:
        """How many envelopes a run may deliver here.

        Derived from ``is_email`` rather than declared, because the two are the
        same fact today and a second field would be one more thing to keep in
        step. It is a property so the call sites read as what they mean -- a
        delivery asking how many sends it gets, not whether the platform
        happens to be email.
        """
        return DeliveryCardinality.ONE if self.is_email else DeliveryCardinality.MANY

    @property
    def finishes_stream_with_answer(self) -> bool:
        """Can a live stream be closed *with* the answer, as one message?

        Only a real streaming API can (Slack's chat.startStream / appendStream /
        stopStream). Everywhere else progress is a separate message that is
        cleared before the answer is sent.
        """
        return self.progress_style is ProgressStyle.STREAM

    @property
    def shows_live_progress(self) -> bool:
        """Does anything at all get shown between the question and the answer?"""
        return self.progress_style is not ProgressStyle.NONE

    @property
    def attachment_byte_cap(self) -> int:
        """Native-attachment hard byte ceiling (reused from ``attachment_limits``)."""
        return attachment_cap(self.platform)

    @property
    def inline_mb_cap(self) -> int:
        """Effective inline cap in MB, as the number to quote to the agent.

        The two surface families measure the file differently, and quoting the
        wrong one is how the prompt came to promise email attachments the
        provider would reject: a chat cap is raw bytes bounded by the soft cap,
        while an email cap is raw bytes whose *base64* form must clear the
        provider ceiling. On a platform with per-media ceilings this is the
        document number — ``media_cap_summary`` carries the smaller kinds.
        """
        effective = (
            email_inline_cap(self.platform)
            if self.is_email
            else inline_cap(self.platform, media_kind=MediaKind.DOCUMENT)
        )
        return effective // (1024 * 1024)

    @property
    def media_cap_note(self) -> str | None:
        """Phrase for kinds capped below ``inline_mb_cap``, or None if uniform."""
        return None if self.is_email else media_cap_summary(self.platform)


_SLACK_FORMATTING = (
    "Write normal Markdown; Lemma delivers it in a Slack markdown block, which "
    "renders headings, tables, ordered/unordered lists, task lists, code fences "
    "with syntax highlighting, block quotes, and [text](url) links natively. Do "
    "not hand-write legacy Slack mrkdwn (single-asterisk bold, <url|label> "
    "links) — it renders literally. Keep replies short; long output reads "
    "better as an attached file."
)
_TEAMS_FORMATTING = (
    "Teams renders a limited markdown subset: bold, italic, bullet/numbered "
    "lists, links, and inline code. Avoid tables and deep nesting — they render "
    "inconsistently."
)
_WHATSAPP_FORMATTING = (
    "WhatsApp formatting: *bold*, _italic_, ~strike~, ```monospace```. No "
    "headings, tables, or labelled links — paste the bare URL. Keep replies "
    "concise and conversational."
)
_TELEGRAM_FORMATTING = (
    "Write normal markdown; Lemma converts it to Telegram MarkdownV2 "
    "automatically. Do not emit raw HTML or hand-escaped MarkdownV2."
)
_EMAIL_FORMATTING = (
    "Write markdown; it is rendered to HTML email. Headings, bullet/numbered "
    "lists, bold/italic, links, and tables are all supported. Structure the "
    "reply clearly as you would a real email."
)


PLATFORM_CAPABILITIES: dict[str, PlatformCapabilities] = {
    "SLACK": PlatformCapabilities(
        platform="SLACK",
        display_name="Slack",
        supports_native_choices=True,
        supports_native_files=True,
        is_email=False,
        is_channel_capable=True,
        reads_channel_history=True,
        markdown_mode="mrkdwn",
        formatting_style=_SLACK_FORMATTING,
        soft_char_limit=3000,
        progress_style=ProgressStyle.STREAM,
    ),
    "TEAMS": PlatformCapabilities(
        platform="TEAMS",
        display_name="Microsoft Teams",
        supports_native_choices=True,
        # False, and not an oversight: `TeamsSurfaceAdapter` never overrides
        # `_render_file`, so the base adapter's stub refuses and every
        # Teams file — any size — is delivered as a link. Claiming True here told
        # the agent its files would arrive as attachments, which they never do.
        # Flip this back the day an outbound Teams file upload exists.
        supports_native_files=False,
        is_email=False,
        is_channel_capable=True,
        reads_channel_history=True,
        markdown_mode="limited_markdown",
        formatting_style=_TEAMS_FORMATTING,
        soft_char_limit=4000,
        progress_style=ProgressStyle.EDIT,
    ),
    "WHATSAPP": PlatformCapabilities(
        platform="WHATSAPP",
        display_name="WhatsApp",
        # Native interactive replies: ≤3 options as buttons, 4–10 as a list.
        # Multi-select / >10 options fall back to formatted text.
        supports_native_choices=True,
        supports_native_files=True,
        is_email=False,
        # In a group it created, answering when addressed (see
        # ``domain/addressing``).
        is_channel_capable=True,
        bot_creates_groups=True,
        markdown_mode="whatsapp",
        formatting_style=_WHATSAPP_FORMATTING,
        soft_char_limit=1500,
        has_shared_system_bot=True,
        # No message-edit API, so a progress update can only be a new message
        # in the person's chat. Rationed hard by the observer: a plan when the
        # agent has one, otherwise a single "still going" on a long run.
        progress_style=ProgressStyle.POST,
        # Meta closes free-form messaging 24h after the person's last message.
        # A notification past that window needs an approved template, which we
        # do not have, so delivery falls through to the next channel.
        reply_window_hours=24,
        # Was `True`, and had to be: one number meant the system credential and
        # the identity were the same thing, so a second surface claiming it in
        # an organisation really was a conflict.
        #
        # A pool separates them. The credential is now the number's, not the
        # deployment's, and an organisation holding two numbers is the feature
        # rather than a collision. Exclusivity did not go away -- it got more
        # precise: `uq_agent_org_whatsapp_number` says one *number* per
        # organisation, which is the rule that was actually wanted, enforced
        # where a race cannot get past it. Leaving this `True` would keep the
        # coarse rule on top and refuse the second number the pool exists to
        # hand out. Resend answers `False` for the same shape of reason: a
        # shared key, an identity allocated per surface.
        system_credential_is_identity=False,
        # Unlike Resend, though, the identity is not always there. A number is
        # bought, so a deployment can own none to allocate -- and a surface
        # holding none is back on the single number in settings, where the old
        # once-per-organization rule is exactly right. See the field.
        system_identity_may_be_absent=True,
    ),
    "TELEGRAM": PlatformCapabilities(
        platform="TELEGRAM",
        display_name="Telegram",
        # Native inline-keyboard ask_user; option taps resolve via a Redis
        # short-token store (64-byte callback_data limit). Multi-select falls
        # back to formatted text.
        supports_native_choices=True,
        supports_native_files=True,
        is_email=False,
        # True, and the adapter is what says so: `TelegramSurfaceAdapter`
        # implements `fetch_thread_context`, the router has a group route, and
        # the parser sets `mentioned_agent` from a bot command. This read False
        # for as long as nothing checked, because nothing in production reads
        # this field -- it only reaches the standing guidance the agent is given,
        # so being wrong here withheld the channel-context section from the one
        # chat platform whose group history is actually fetched and injected.
        is_channel_capable=True,
        markdown_mode="markdownv2_converted",
        formatting_style=_TELEGRAM_FORMATTING,
        soft_char_limit=3500,
        has_shared_system_bot=True,
        supports_native_voice=True,
        progress_style=ProgressStyle.EDIT,
        # A DM's live update is a thinking chip — one line, newlines collapsed.
        # A group's is a plain edited message, which would hold a checklist, but
        # a platform showing two different shapes of the same update is worth
        # less than either shape: the DM is where nearly every run is watched,
        # and one line is what a DM can show.
        progress_is_one_line=True,
    ),
    "RESEND": PlatformCapabilities(
        platform="RESEND",
        display_name="Email",
        supports_native_choices=False,
        supports_native_files=True,
        is_email=True,
        is_channel_capable=False,
        markdown_mode="html_rendered",
        formatting_style=_EMAIL_FORMATTING,
        soft_char_limit=6000,
        can_cold_open=True,
        # One API key, a catch-all domain, and a unique address per surface.
        # Sharing the key across pods is the point, not a conflict.
        system_credential_is_identity=False,
    ),
}


def get_platform_capabilities(platform: str | None) -> PlatformCapabilities | None:
    """Return the capabilities for a platform (case-insensitive), or ``None``."""
    if not platform:
        return None
    return PLATFORM_CAPABILITIES.get(str(platform).upper())


def bot_creates_groups_on(platform: str | None) -> bool:
    """Is every group this platform's bot is in one it created? (WhatsApp)"""
    capabilities = get_platform_capabilities(platform)
    return capabilities is not None and capabilities.bot_creates_groups


def has_shared_system_bot(platform: str | None) -> bool:
    """Does an inbound platform-wide webhook here arrive on Lemma's shared bot?

    The single answer to "may this webhook be narrowed to system-credential
    surfaces", so no caller keeps its own list of platforms. An unknown platform
    has none.
    """
    capabilities = get_platform_capabilities(platform)
    return capabilities is not None and capabilities.has_shared_system_bot


def system_credential_claim_applies(
    platform: str | None, *, holds_own_identity: bool
) -> bool:
    """Does "claimable once per organization" apply to this system credential?

    The write-side refusal and the catalog that greys the option out both ask
    this, and they have to agree: a catalog that disagrees with the writer
    either offers something that then fails, or hides something that would have
    worked.

    Two facts decide it. ``system_credential_is_identity`` says whether the
    deployment's credential *is* the thing inbound is keyed on, and where it is,
    the rule always applies. Where it is not, the surface has an identity of its
    own instead -- and ``holds_own_identity`` says whether this one actually got
    one, because on WhatsApp that depends on there being a pool to draw from.
    A surface that got none is on the deployment's single number, so the rule
    applies to it after all.

    An unknown platform gets the rule. Not knowing what a credential is, is not
    a reason to stop guarding it.
    """
    capabilities = get_platform_capabilities(platform)
    if capabilities is None or capabilities.system_credential_is_identity:
        return True
    return capabilities.system_identity_may_be_absent and not holds_own_identity


# Platforms whose native voice note wants OGG/Opus (a proper voice bubble);
# everything else gets MP3 (inline audio player / file attachment).
_OGG_VOICE_PLATFORMS = {"TELEGRAM", "WHATSAPP"}


def voice_note_format(platform: str | None) -> str:
    """TTS output format for a native voice note on ``platform`` ("ogg"|"mp3")."""
    return "ogg" if str(platform or "").upper() in _OGG_VOICE_PLATFORMS else "mp3"
