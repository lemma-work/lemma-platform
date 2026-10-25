"""What the brief actually says, as against how it is assembled and cached.

``test_agent_context_brief`` covers connection discipline and the two caches.
This covers the rendering: the three things an agent could not previously read
off its own context, each of which it was nonetheless expected to act on.
"""

from types import SimpleNamespace

import pytest

from app.modules.agent.domain.run_state import RunState
from app.modules.agent.domain.value_objects import ConversationOrigin, RunTrigger
from app.modules.agent.services import agent_self_brief as self_mod
from app.modules.agent.services import brief_lines
from app.modules.datastore.domain.datastore_entities import (
    ColumnSchema,
    DatastoreDataType,
    ForeignKeySpec,
)

pytestmark = pytest.mark.unit


def _column(name, type_, **kwargs):
    return ColumnSchema(name=name, type=DatastoreDataType(type_), **kwargs)


class TestCompactTableSchema:
    """Compression preserves row scope independently of resource visibility."""

    def _table(self, *, enable_rls: bool, visibility: str | None = None):
        return SimpleNamespace(
            table_name="tickets",
            primary_key_column="id",
            enable_rls=enable_rls,
            visibility=visibility,
            columns=[_column("id", "UUID")],
        )

    def test_rls_is_explicit_when_enabled(self):
        line = brief_lines.table_line(self._table(enable_rls=True))
        assert "rls=on" in line

    def test_primary_key_is_not_assumed_to_be_id(self):
        table = self._table(enable_rls=True)
        table.primary_key_column = "ticket_number"
        assert "pk=ticket_number" in brief_lines.table_line(table)

    def test_rls_off_does_not_imply_public_visibility(self):
        line = brief_lines.table_line(self._table(enable_rls=False))
        assert "rls=off" in line
        assert "visibility=" not in line

    def test_visibility_is_stated_separately_from_row_scope(self):
        """Turning RLS off does not make a RESTRICTED table readable."""
        line = brief_lines.table_line(
            self._table(enable_rls=False, visibility="RESTRICTED")
        )
        assert "visibility=RESTRICTED" in line
        assert "rls=off" in line

    def test_a_table_that_does_not_say_is_read_as_the_default(self):
        """Absent means on, because that is what the datastore does with it."""
        bare = SimpleNamespace(
            table_name="tickets", primary_key_column="id", columns=[]
        )
        assert "rls=on" in brief_lines.table_line(bare)


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
            _column("pod_id", "UUID", foreign_key=ForeignKeySpec(references="pods.id"))
        )
        assert "pods.id" in spec

    def test_descriptions_do_not_expand_the_inventory(self):
        """Full descriptions remain on the table's inspection endpoint."""
        spec = brief_lines.column_spec(
            _column(
                "status",
                "TEXT",
                required=True,
                description="open, held, or closed" * 100,
            )
        )
        assert spec == "status:TEXT(required)"


class TestTheRunSaysWhetherAnybodyIsWaiting:
    """Every run used to read identically, which is the original bug.

    A person typing, a message from Slack and a schedule firing at six in the
    morning produced the same prompt, so an unattended run would call
    ``ask_user`` and hang on an answer nobody was going to give.

    Two corrections since, and both are regressions worth pinning: reading the
    conversation alone, and then classifying run sources by an allow-list of
    human ones that ``approval_resume`` was missing from.
    """

    @staticmethod
    def _scheduled() -> SimpleNamespace:
        return SimpleNamespace(
            origin=ConversationOrigin.SCHEDULE,
            metadata={"schedule_name": "daily-invoices"},
        )

    def test_a_scheduled_firing_is_told_nobody_is_watching(self):
        framed = brief_lines.run_framing(
            self._scheduled(), run_trigger=RunTrigger.SCHEDULE_FIRE
        )
        assert "daily-invoices" in framed
        assert "Assume nobody is watching" in framed

    def test_the_run_says_where_it_was_asked_from_its_own_platform(self):
        """The conversation was last heard on WhatsApp; this run was asked in
        Lemma, and its framing must not say it arrived from WhatsApp."""
        heard = SimpleNamespace(metadata={"surface_platform": "WHATSAPP"})

        in_lemma = brief_lines.run_framing(heard, run_trigger=RunTrigger.MESSAGE)
        on_whatsapp = brief_lines.run_framing(
            heard, run_trigger=RunTrigger.MESSAGE, platform="WHATSAPP"
        )

        assert "whatsapp" not in in_lemma.lower()
        assert "This arrived from whatsapp" in on_whatsapp

    def test_a_person_typing_into_a_scheduled_conversation_is_not_ignored(self):
        """A schedule opened the conversation; a person may return to it."""
        framed = brief_lines.run_framing(
            self._scheduled(), run_trigger=RunTrigger.MESSAGE
        )
        assert "Assume nobody is watching" not in framed
        assert "a person is now asking" in framed

    def test_an_approval_resume_is_a_person_answering(self):
        """The reproduction that forced the second correction.

        Approving, or answering `ask_user`, resumes the run. Classified against
        an allow-list of human sources that did not contain it, the run that
        exists *because* somebody just responded was told nothing had come from
        a person — so it would carry on as though unattended and file its
        answer away from them.
        """
        framed = brief_lines.run_framing(
            self._scheduled(), run_trigger=RunTrigger.RESUME
        )
        assert "Assume nobody is watching" not in framed
        assert "A person answered what you were waiting on" in framed

    def test_an_approval_resume_says_so_outside_a_schedule_too(self):
        """Most approvals happen in an ordinary conversation."""
        framed = brief_lines.run_framing(
            SimpleNamespace(metadata={}), run_trigger=RunTrigger.RESUME
        )
        assert "A person answered what you were waiting on" in framed

    def test_a_wake_is_a_timer_not_a_person(self):
        """The other resume: nobody pressed anything, a clock came due."""
        framed = brief_lines.run_framing(self._scheduled(), run_trigger=RunTrigger.WAKE)
        assert "Assume nobody is watching" in framed

    @pytest.mark.parametrize(
        "trigger",
        [
            RunTrigger.MESSAGE,
            RunTrigger.FOLLOW_UP,
            RunTrigger.RETRY,
            RunTrigger.REPLIES,
            RunTrigger.DELEGATION_RESULT,
        ],
    )
    def test_only_a_known_automatic_trigger_is_called_unattended(self, trigger):
        """The list is of *automatic* triggers, and that direction is the point.

        Claiming nobody is there is the failure that costs somebody an answer.
        This is the shape that would have caught the resume.
        """
        framed = brief_lines.run_framing(self._scheduled(), run_trigger=trigger)
        assert "Assume nobody is watching" not in framed

    def test_the_unattended_wording_does_not_claim_the_reply_vanishes(self):
        """Replies are persisted. Nobody reading it *now* is the honest claim."""
        framed = brief_lines.run_framing(
            self._scheduled(), run_trigger=RunTrigger.SCHEDULE_FIRE
        )
        assert "still saved to this conversation" in framed

    def test_a_surface_run_is_told_where_the_person_is(self):
        conversation = SimpleNamespace(metadata={"surface_platform": "SLACK"})
        framed = brief_lines.run_framing(conversation, platform="SLACK")
        assert "arrived from slack" in framed

    def test_an_unmarked_run_is_told_its_budget_and_nothing_guessed_at(self):
        """Somebody typing is the common case: no provenance line, but a budget.

        The budget reaches every run on purpose. The study's first finding was
        that nothing in the brief says stop, and the common case is exactly the
        one that must not be the exception — a run that knows it has twenty
        minutes can pick the short route.
        """
        conversation = SimpleNamespace(metadata={})

        framing = brief_lines.run_framing(conversation)

        assert framing.startswith("## This run")
        assert "steps" in framing and "minutes" in framing
        # The number never travels alone. Stated bare, a ceiling reads as a
        # target and the run hurries to fit it; the limit is only there for a
        # run going in circles. What a watched run is told instead is where its
        # time actually went: extra checking, not the work.
        assert "A person is waiting for this reply" in framing
        assert "rather than rendering" in framing
        assert "still gets the time it needs" in framing
        assert "shortest route" not in framing
        # Nothing is claimed about who started it, or whether anybody is waiting.
        assert "schedule" not in framing.lower()
        assert "nobody is watching" not in framing.lower()

    def test_an_unattended_run_is_told_to_take_the_time_it_needs(self):
        conversation = SimpleNamespace(origin=ConversationOrigin.SCHEDULE, metadata={})

        framing = brief_lines.run_framing(conversation, run_trigger=RunTrigger.WAKE)

        assert "Take the time the work needs" in framing
        assert "A person is waiting" not in framing

    def test_a_conversation_with_no_metadata_at_all_is_safe(self):
        framing = brief_lines.run_framing(SimpleNamespace())

        assert framing.startswith("## This run")
        assert "nobody is watching" not in framing.lower()


class TestTheRunTriggerComesOffTheRun:
    """The conversation cannot answer this; only the run can."""

    def test_the_trigger_is_read_from_the_run(self):
        run = SimpleNamespace(state=RunState(trigger=RunTrigger.RESUME))
        assert brief_lines.run_trigger_of(run) is RunTrigger.RESUME

    def test_a_run_without_state_has_no_trigger(self):
        assert brief_lines.run_trigger_of(SimpleNamespace()) is None
        assert brief_lines.run_trigger_of(SimpleNamespace(state=None)) is None


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


class TestAColumnSaysWhatItWillAccept:
    """The brief dropped a TEXT column's length and an ENUM's values, and
    nothing said a column called `check` needs quoting -- so the agent learned
    each from a refused write or a SQL syntax error."""

    def test_a_bounded_text_column_shows_its_length(self):
        spec = brief_lines.column_spec(_column("title", "TEXT", max_length=500))
        assert spec == "title:VARCHAR(500)"

    def test_an_enum_lists_its_values(self):
        spec = brief_lines.column_spec(
            _column("status", "ENUM", options=["open", "held", "closed"])
        )
        assert spec == "status:ENUM[open|held|closed]"

    def test_a_long_enum_is_cut_and_says_how_much(self):
        spec = brief_lines.column_spec(
            _column("code", "ENUM", options=[f"c{i}" for i in range(20)])
        )
        assert "…+8" in spec

    def test_a_default_is_shown(self):
        spec = brief_lines.column_spec(
            _column("status", "ENUM", options=["open", "held"], default="open")
        )
        assert "default=open" in spec

    def test_a_name_sql_cannot_read_unquoted_is_shown_quoted(self):
        assert brief_lines.column_spec(_column("check", "TEXT")).startswith('"check":')
        assert brief_lines.column_spec(_column("Order", "TEXT")).startswith('"Order":')
        assert brief_lines.column_spec(_column("amount", "FLOAT")) == "amount:FLOAT"
