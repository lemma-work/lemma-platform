"""What the brief actually says, as against how it is assembled and cached.

``test_agent_context_brief`` covers connection discipline and the two caches.
This covers the rendering: the three things an agent could not previously read
off its own context, each of which it was nonetheless expected to act on.
"""

from types import SimpleNamespace

import pytest

from app.modules.agent.services import agent_self_brief as self_mod
from app.modules.agent.services import brief_lines

pytestmark = pytest.mark.unit


def _column(name, type_, **kwargs):
    return SimpleNamespace(
        name=name,
        type=type_,
        required=kwargs.get("required", False),
        unique=kwargs.get("unique", False),
        system=kwargs.get("system", False),
        auto=kwargs.get("auto", False),
        description=kwargs.get("description"),
        foreign_key=kwargs.get("foreign_key"),
    )


class TestATableLineSaysWhoCanSeeTheRows:
    """``enable_rls`` defaults to on, and the brief never said so.

    The agent is told that anything with a status, an owner or a lifecycle
    belongs in a table row it creates and updates. Create one without a thought
    about RLS and each member sees only their own rows — so it builds the team's
    ledger as one person's private notebook and reports that the team can now
    see it.

    Row scope and table visibility are two different facts, and the first
    version of this line ran them together: it said "everyone sees the same
    rows" for any RLS-off table, which promises read access to a table that may
    be RESTRICTED.
    """

    def _table(self, *, enable_rls: bool, visibility: str | None = None):
        return SimpleNamespace(
            table_name="tickets",
            primary_key_column="id",
            enable_rls=enable_rls,
            visibility=visibility,
            columns=[_column("id", "UUID")],
        )

    def test_an_rls_table_says_rows_are_per_person(self):
        line = brief_lines.table_line(self._table(enable_rls=True))
        assert "RLS on" in line
        assert "you see your own" in line

    def test_an_rls_table_does_not_claim_other_rows_do_not_exist(self):
        """There is a permission-checked admin mode; the rows are not gone."""
        line = brief_lines.table_line(self._table(enable_rls=True))
        assert "admin mode" in line

    def test_a_shared_table_scopes_its_promise_to_who_can_read_it(self):
        line = brief_lines.table_line(self._table(enable_rls=False))
        assert "RLS off" in line
        assert "everyone who can read this table" in line

    def test_visibility_is_stated_separately_from_row_scope(self):
        """Turning RLS off does not make a RESTRICTED table readable."""
        line = brief_lines.table_line(
            self._table(enable_rls=False, visibility="RESTRICTED")
        )
        assert "visibility RESTRICTED" in line
        assert "RLS off" in line

    def test_a_table_that_does_not_say_is_read_as_the_default(self):
        """Absent means on, because that is what the datastore does with it."""
        bare = SimpleNamespace(
            table_name="tickets", primary_key_column="id", columns=[]
        )
        assert "RLS on" in brief_lines.table_line(bare)


class TestAColumnCarriesWhatAWriteNeeds:
    """``name:type`` loses the four facts that decide whether a write works."""

    def test_a_required_column_is_marked(self):
        spec = brief_lines.column_spec(_column("title", "TEXT", required=True))
        assert "(required)" in spec

    def test_a_system_column_is_marked_auto_rather_than_required(self):
        """``user_id`` and ``created_at`` are required *and* filled in for you.

        Marked required, an agent supplies them and the write comes back
        rejected. The useful fact is that it must not.
        """
        spec = brief_lines.column_spec(
            _column("user_id", "UUID", required=True, system=True)
        )
        assert "(auto)" in spec
        assert "required" not in spec

    def test_a_foreign_key_names_what_it_points_at(self):
        spec = brief_lines.column_spec(
            _column("pod_id", "UUID", foreign_key=SimpleNamespace(references="pods.id"))
        )
        assert "pods.id" in spec

    def test_the_builders_own_description_survives(self):
        """The one place a pod explains its data model in words."""
        spec = brief_lines.column_spec(
            _column("status", "TEXT", description="open, held, or closed")
        )
        assert '"open, held, or closed"' in spec


class TestTheRunSaysWhetherAnybodyIsWaiting:
    """Every run used to read identically, which is the whole bug.

    A person typing, a message arriving from Slack and a schedule firing at six
    in the morning produced the same prompt. So an unattended run would call
    ``ask_user`` and hang on an answer nobody was going to give.

    The first fix then introduced a second bug, which is what the rest of this
    covers: it read the *conversation*, and a schedule stamps the conversation
    once and forever.
    """

    SCHEDULED = {"started_by": "SCHEDULE", "schedule_name": "daily-invoices"}

    def test_a_scheduled_firing_is_told_nobody_is_watching(self):
        framed = brief_lines.with_run_framing(
            "BRIEF", conversation=SimpleNamespace(metadata=self.SCHEDULED)
        )
        assert "daily-invoices" in framed
        assert "Assume nobody is watching" in framed
        assert "ask_user" in framed

    def test_a_person_typing_into_a_scheduled_conversation_is_not_ignored(self):
        """The bug this class exists for.

        ``started_by`` stays on the conversation. Somebody opening it the next
        morning and asking a question starts a new run in it — and the agent was
        being told nobody would read the reply, so it filed its answer and said
        nothing to the person who had just asked.
        """
        framed = brief_lines.with_run_framing(
            "BRIEF",
            conversation=SimpleNamespace(metadata=self.SCHEDULED),
            run_source="user_message",
        )
        assert "Assume nobody is watching" not in framed
        assert "a person is now asking" in framed

    def test_queued_messages_also_count_as_a_person(self):
        """A message queued behind a running turn is still somebody typing."""
        framed = brief_lines.with_run_framing(
            "BRIEF",
            conversation=SimpleNamespace(metadata=self.SCHEDULED),
            run_source="queued_messages",
        )
        assert "Assume nobody is watching" not in framed

    def test_an_unrecognised_run_source_does_not_count_as_a_person(self):
        """One-sided on purpose.

        A source this build does not know is not evidence somebody is there, so
        the unattended framing stands — but a *recognised* human source always
        wins, because wrongly claiming nobody is watching is the failure that
        costs somebody an answer.
        """
        framed = brief_lines.with_run_framing(
            "BRIEF",
            conversation=SimpleNamespace(metadata=self.SCHEDULED),
            run_source="some_future_trigger",
        )
        assert "Assume nobody is watching" in framed

    def test_the_unattended_wording_does_not_claim_the_reply_vanishes(self):
        """Replies are persisted. Nobody reading it *now* is the honest claim."""
        framed = brief_lines.with_run_framing(
            "BRIEF", conversation=SimpleNamespace(metadata=self.SCHEDULED)
        )
        assert "still saved to this conversation" in framed

    def test_a_surface_run_is_told_where_the_person_is(self):
        conversation = SimpleNamespace(metadata={"surface_platform": "SLACK"})
        framed = brief_lines.with_run_framing("BRIEF", conversation=conversation)
        assert "arrived from slack" in framed

    def test_an_unmarked_run_is_told_nothing_rather_than_guessed_at(self):
        """Somebody typing is the common case and needs no line at all."""
        conversation = SimpleNamespace(metadata={})
        assert (
            brief_lines.with_run_framing("BRIEF", conversation=conversation) == "BRIEF"
        )

    def test_a_conversation_with_no_metadata_at_all_is_safe(self):
        assert (
            brief_lines.with_run_framing("BRIEF", conversation=SimpleNamespace())
            == "BRIEF"
        )


class TestAScheduleReadsLikeAJob:
    """Two storage shapes, one kind of fact to whoever reads the brief."""

    def _summary(self, **kwargs):
        return SimpleNamespace(
            name=kwargs.get("name", "morning-sweep"),
            schedule_type=kwargs.get("schedule_type", "TIME"),
            instruction=kwargs.get("instruction"),
            agent_id=kwargs.get("agent_id"),
            workflow_id=None,
            is_active=kwargs.get("is_active", True),
            config=kwargs.get("config", {}),
        )

    def test_a_cron_schedule_names_its_expression_and_zone(self):
        line = self_mod.schedule_line(
            self._summary(config={"cron": "0 9 * * 1-5", "timezone": "Europe/Berlin"})
        )
        assert "cron `0 9 * * 1-5` (Europe/Berlin)" in line

    def test_a_table_trigger_names_the_table_and_the_operations(self):
        line = self_mod.schedule_line(
            self._summary(
                schedule_type="DATASTORE",
                config={"table_name": "tickets", "operations": ["INSERT", "UPDATE"]},
            )
        )
        assert "on insert, update in `tickets`" in line

    def test_a_table_trigger_with_no_operations_says_any_change(self):
        line = self_mod.schedule_line(
            self._summary(schedule_type="DATASTORE", config={"table_name": "tickets"})
        )
        assert "on any change in `tickets`" in line

    def test_a_paused_schedule_says_so(self):
        """A paused job is not standing work, and acting on one is a mistake."""
        line = self_mod.schedule_line(self._summary(is_active=False))
        assert "(paused)" in line
