"""Every producer of a channel line, and the test proving its delivery rows.

A line reaches somebody outside the app only through a
``channel_message_deliveries`` row: ``Timeline.post`` plans one for each place
it is carried to (``deliver_to``) and each member pushed. A producer -- a
module that posts lines -- that meant a line for a phone or a platform and
planned none would leave it unsent with nothing to say so. So each producer is
listed here with what it sends and the real-database test that watches its
rows; a new call site of ``post`` / ``write_as_member`` / ``MemberLines.post``
fails this until it is listed, and a proof that is renamed or deleted fails it
too.

What a producer sends:

* ``PLACE`` -- carried to an outside place: a row per place;
* ``PUSH`` -- a line in the app a member is told of: a row per member pushed;
* ``HEARD`` -- said at a place already, so never carried back there: it
  passes no ``deliver_to`` (a member may still be pushed of it), which this
  file checks -- there is no row to watch;
* ``NOTE`` -- a members-only note: no row at all.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

BACKEND = Path(__file__).resolve().parents[5]
MODULES = BACKEND / "app" / "modules"

#: Where a line is posted from, but not a producer: the channels module's own
#: implementation of the contracts the producers call.
_IMPLEMENTATION = ("channels/contracts/", "channels/services/")

#: The entry points a producer posts a line through.
_POSTERS = frozenset({"post", "write_as_member"})
_POSTING_CLASSES = frozenset({"MemberLines", "Timeline"})


class Sends(Enum):
    PLACE = "place"
    PUSH = "push"
    HEARD = "heard"
    NOTE = "note"


@dataclass(frozen=True)
class Producer:
    sends: Sends
    #: ``path/to/test_file.py::test_name``, under ``app/modules``; ``None`` for
    #: a line heard at a place, which this file's own check holds.
    proof: str | None = None


_SURFACES = "agent_surfaces/tests/e2e"
_CHANNELS = "channels/tests/e2e"
_PRODUCERS_E2E = f"{_CHANNELS}/test_delivery_producers_e2e.py"

PRODUCERS: dict[str, Producer] = {
    # -- carried to a place ---------------------------------------------------
    "agent_surfaces/services/run_reply_lines.py:post_run_reply": Producer(
        Sends.PLACE,
        f"{_SURFACES}/test_run_answer_deliveries_e2e.py"
        "::test_a_run_finished_twice_is_one_line_and_one_send",
    ),
    "agent_surfaces/services/contact_acknowledgement.py:acknowledge_contact": Producer(
        Sends.PLACE,
        f"{_SURFACES}/test_contact_acknowledgement_e2e.py"
        "::test_a_waiting_contact_is_told_once_until_a_member_writes",
    ),
    "agent_surfaces/services/outside_cap.py:_courtesy_line": Producer(
        Sends.PLACE,
        f"{_SURFACES}/test_contact_care_e2e.py"
        "::test_past_the_cap_no_run_starts_and_a_person_takes_over",
    ),
    "agent_surfaces/services/contact_outreach.py:ContactOutreach.write": Producer(
        Sends.PLACE,
        f"{_SURFACES}/test_contact_outreach_e2e.py"
        "::test_an_email_contact_is_written_to_where_they_wrote",
    ),
    "channels/api/message_controller.py:post_message": Producer(
        Sends.PLACE,
        f"{_CHANNELS}/test_heard_and_carried_e2e.py"
        "::test_a_member_line_carried_to_a_mirrored_group_plans_its_place_row",
    ),
    "agent/services/channel_answer_lines.py:_post_line": Producer(
        Sends.PLACE,
        f"{_CHANNELS}/test_heard_and_carried_e2e.py"
        "::test_an_agent_answer_to_a_carried_group_line_follows_it_to_the_place",
    ),
    # -- pushed to a member ---------------------------------------------------
    "agent_surfaces/services/direct_messages.py:DirectMessages.send": Producer(
        Sends.PUSH,
        f"{_SURFACES}/test_channel_deliveries_e2e.py"
        "::test_a_line_is_in_their_channel_and_its_push_says_why_it_went_no_further",
    ),
    "agent/tools/messaging/pydantic_adapter.py:_post_to_member": Producer(
        Sends.PUSH,
        f"{_PRODUCERS_E2E}::test_an_agent_writing_to_a_member_plans_their_push",
    ),
    "agent/tools/messaging/pydantic_adapter.py:_post_in_channel": Producer(
        Sends.PUSH,
        f"{_PRODUCERS_E2E}"
        "::test_an_agent_writing_in_its_members_channel_plans_their_push",
    ),
    "agent/services/pause_questions.py:_ask": Producer(
        Sends.PUSH,
        f"{_PRODUCERS_E2E}::test_a_paused_run_asks_its_member_and_plans_their_push",
    ),
    "workflow/infrastructure/form_questions.py:FormQuestions.ask_form": Producer(
        Sends.PUSH,
        f"{_PRODUCERS_E2E}::test_a_workflow_form_plans_a_push_to_whom_it_is_assigned",
    ),
    "agent/tools/messaging/people.py:call_a_person_in": Producer(
        Sends.PUSH,
        f"{_PRODUCERS_E2E}"
        "::test_a_contacts_run_calling_a_person_in_plans_the_keepers_push",
    ),
    # -- heard at a place -----------------------------------------------------
    "agent_surfaces/services/channel_lines.py:note_heard": Producer(Sends.HEARD),
    "agent_surfaces/services/channel_lines.py:note_heard_on_web": Producer(Sends.HEARD),
    "agent_surfaces/services/group_log.py:GroupLog.note_inbound": Producer(Sends.HEARD),
    # -- a note, never sent ---------------------------------------------------
    "agent/services/pause_questions.py:tell_where_asked": Producer(
        Sends.NOTE,
        "agent/tests/e2e/test_channel_answers_e2e.py"
        "::test_an_approved_action_that_failed_is_said_on_the_timeline",
    ),
    "agent_surfaces/services/direct_messages.py:tell_member": Producer(
        Sends.NOTE,
        f"{_PRODUCERS_E2E}::test_a_notice_is_a_note_in_their_channel_and_never_sent",
    ),
    "agent_surfaces/services/opt_out_notes.py:note_opt_out": Producer(
        Sends.NOTE,
        f"{_SURFACES}/test_contact_care_e2e.py"
        "::test_stop_unsubscribes_the_address_it_came_from",
    ),
}


@dataclass(frozen=True)
class CallSite:
    where: str
    call: ast.Call


def _posting_names(tree: ast.Module) -> set[str]:
    """The names this module posts lines through, as it imported them."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module is None:
            continue
        if node.module.startswith("app.modules.channels.contracts"):
            names |= {a.asname or a.name for a in node.names if a.name in _POSTERS}
        if node.module.startswith("app.modules.channels.services"):
            names |= {
                a.asname or a.name for a in node.names if a.name in _POSTING_CLASSES
            }
    return names


def _is_posting(call: ast.Call, names: set[str]) -> bool:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id in names and func.id not in _POSTING_CLASSES
    # ``MemberLines(uow).post(...)``: the class's own posting method.
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "post"
        and isinstance(func.value, ast.Call)
        and isinstance(func.value.func, ast.Name)
        and func.value.func.id in names & _POSTING_CLASSES
    )


def _call_sites() -> list[CallSite]:
    found: list[CallSite] = []
    for path in sorted(MODULES.rglob("*.py")):
        relative = path.relative_to(MODULES).as_posix()
        if "/tests/" in relative or relative.startswith("test_support/"):
            continue
        if relative.startswith(_IMPLEMENTATION):
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        names = _posting_names(tree)
        if not names:
            continue

        def visit(node: ast.AST, scope: str) -> None:
            for child in ast.iter_child_nodes(node):
                inner = scope
                if isinstance(
                    child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                ):
                    inner = f"{scope}.{child.name}" if scope else child.name
                if isinstance(child, ast.Call) and _is_posting(child, names):
                    found.append(CallSite(f"{relative}:{inner}", child))
                visit(child, inner)

        visit(tree, "")
    return found


def _keywords(call: ast.Call) -> set[str]:
    return {keyword.arg for keyword in call.keywords if keyword.arg}


def test_every_producer_of_a_line_is_listed_with_what_it_sends():
    sites = {site.where for site in _call_sites()}
    unlisted = sorted(sites - PRODUCERS.keys())
    gone = sorted(PRODUCERS.keys() - sites)
    assert not unlisted, (
        "A new producer of channel lines: list it in PRODUCERS with what it "
        f"sends and the real-database test proving its delivery rows: {unlisted}"
    )
    assert not gone, f"Listed producers that no longer post a line: {gone}"


def test_a_line_heard_at_a_place_or_kept_in_the_app_is_carried_nowhere():
    """``HEARD`` and ``NOTE`` producers pass no ``deliver_to``; a ``PLACE``
    producer posting through ``post`` names where it goes."""
    for site in _call_sites():
        sends = PRODUCERS[site.where].sends
        keywords = _keywords(site.call)
        if sends in (Sends.HEARD, Sends.NOTE):
            assert "deliver_to" not in keywords, site.where
        if sends is Sends.PLACE and isinstance(site.call.func, ast.Name):
            if site.call.func.id == "post":
                assert "deliver_to" in keywords, site.where


@pytest.mark.parametrize("where", sorted(PRODUCERS))
def test_each_producer_names_a_test_that_exists(where):
    producer = PRODUCERS[where]
    if producer.sends is Sends.HEARD:
        assert producer.proof is None, "a heard line has no row to prove"
        return
    assert producer.proof is not None, f"{where}: which test watches its rows?"
    path, _, name = producer.proof.partition("::")
    file = MODULES / path
    assert file.is_file(), f"{where}: no {path}"
    tree = ast.parse(file.read_text(), filename=str(file))
    tests = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert name in tests, f"{where}: {path} has no {name}"
