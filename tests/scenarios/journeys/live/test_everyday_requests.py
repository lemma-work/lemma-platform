"""Live → the everyday things a person asks the pod's agent for, and how long they wait.

The fast lane proves an agent run happens; it cannot prove one is quick,
because its model is scripted. This is the half only a real model answers:
the requests people actually make of a pod every day, each given the budget
PS-AGENT-016 promises and checked for having done the thing, not only for
having replied.

Each scenario makes its own data under a run-unique name, sends the request the
way the frontend does -- appended, not streamed, so the scenario's own request
timeout is never a ceiling on the model -- and waits for the run to settle. The
clock is the person's: from sending the message to the run being over.
"""

from __future__ import annotations

import csv
import io
import json
import re
import time
import zipfile

import pytest

from harness import capability, covers, journey, proves, scenario
from harness.environment import MODEL_IS_REAL
from harness.credentials import needs
from harness.run import a_name_for
from harness.steps.datastore import column

pytestmark = [
    journey("Agents and conversations"),
    capability("Talk to an agent"),
    pytest.mark.live,
]

#: PS-AGENT-016's budgets, in seconds.
QUICK = 30.0
FILE_OR_WEB = 45.0
WIDGET_OF_ITS_OWN = 60.0
DOCUMENT = 90.0
RESEARCH = 120.0

ORDERS = [
    {"customer": "Acme", "units": 3, "status": "open"},
    {"customer": "Globex", "units": 12, "status": "shipped"},
    {"customer": "Initech", "units": 5, "status": "open"},
    {"customer": "Umbrella", "units": 1, "status": "cancelled"},
    {"customer": "Hooli", "units": 8, "status": "open"},
    {"customer": "Stark", "units": 20, "status": "shipped"},
    {"customer": "Wayne", "units": 2, "status": "open"},
]
OPEN = [o for o in ORDERS if o["status"] == "open"]


@pytest.fixture
async def desk(world):
    """A person in a pod with a small orders table in it."""
    needs(MODEL_IS_REAL)
    person = await world.person("daniel")
    pod = await person.works_in("customer-support")
    table = a_name_for("orders").replace("-", "_")
    await person.creates_a_table(
        in_pod=pod,
        named=table,
        columns=[column("customer"), column("units", "INTEGER"), column("status")],
        shared=True,
    )
    await person.adds_records(ORDERS, to_table=table, in_pod=pod)
    return person, pod, table


async def _asks(person, pod, message: str, *, budget: float, **conversation) -> str:
    """Send one request, wait for the run to end, hold it to its budget.

    Returns the agent's reply text, for the scenario to check it said the thing.
    """
    started = time.monotonic()
    thread = await person.api.post(
        f"/pods/{pod['id']}/conversations",
        what=f"{person.label} opening a conversation",
        json=conversation,
    )
    await person.adds_while_it_works(message, in_conversation=thread, in_pod=pod)
    await person.waits_for_a_reply(in_conversation=thread, in_pod=pod, timeout=budget * 3)
    settled = await person.waits_for_the_run_to_settle(
        conversation=thread, in_pod=pod, timeout=budget * 3
    )
    took = time.monotonic() - started
    status = str(settled.get("status") or "").upper()
    assert status not in {"FAILED", "ERROR"}, f"the run ended {status}: {settled}"
    transcript = await person.transcript_of(thread, in_pod=pod)
    assert took <= budget, (
        f"{message!r} took {took:.1f}s against a {budget:.0f}s budget.\n{transcript[-1500:]}"
    )
    return transcript


def _links(text: str) -> int:
    return len(set(re.findall(r"https?://[^\s)\]>]+", text)))


@scenario("A question about the pod's data is answered while the person waits")
@proves("PS-AGENT-016")
@covers(
    "agent.conversation.create",
    "agent.conversation.message.append",
    "agent.conversation.get",
)
async def test_a_question_about_data(desk):
    person, pod, table = desk

    reply = await _asks(
        person, pod, f"How many orders in the {table} table are still open?", budget=QUICK
    )

    assert str(len(OPEN)) in reply, (
        f"expected {len(OPEN)} open orders in:\n{reply[-800:]}"
    )


@scenario("A record asked for is added while the person waits")
@proves("PS-AGENT-016")
@covers(
    "agent.conversation.create",
    "agent.conversation.message.append",
    "agent.conversation.get",
)
async def test_a_record_is_added(desk):
    person, pod, table = desk

    await _asks(
        person,
        pod,
        f"Add an order to {table}: Soylent, 4 units, status open.",
        budget=QUICK,
    )

    rows = await person.records_in(table, in_pod=pod)
    assert any(r.get("customer") == "Soylent" and r.get("units") == 4 for r in rows), rows


@scenario("A doc is changed only where asked, while the person waits")
@proves("PS-AGENT-016")
@covers(
    "agent.conversation.create",
    "agent.conversation.message.append",
    "agent.conversation.get",
)
async def test_a_doc_is_edited_in_place(desk):
    person, pod, _ = desk
    name = a_name_for("plan") + ".md"
    before = (
        "# Launch plan — draft\n\n"
        "We ship the beta to ten design partners in October.\n\n"
        "## Risks\n\n- Onboarding copy is untested.\n"
    )
    await person.uploads(
        content=before.encode(), named=name, in_pod=pod, content_type="text/markdown"
    )
    path = f"/{name}"

    await _asks(
        person,
        pod,
        "Change the title from 'draft' to 'final'. Nothing else.",
        budget=QUICK,
        title=f"Doc: {name}",
        type="PROJECT",
        instructions=f"This conversation is attached to the doc `{path}`; a change "
        "without a place named is a change to that doc.",
        metadata={"lemma_attached_file": path},
    )

    after = (await person.downloads(path, in_pod=pod)).decode()
    assert after == before.replace("Launch plan — draft", "Launch plan — final"), after


@scenario("A file made from the pod's data lands where it was asked for")
@proves("PS-AGENT-016")
@covers(
    "agent.conversation.create",
    "agent.conversation.message.append",
    "agent.conversation.get",
)
async def test_a_csv_is_exported(desk):
    person, pod, table = desk
    path = f"/me/exports/{table}-open.csv"

    await _asks(
        person,
        pod,
        f"Export the open orders in {table} as a CSV to {path}.",
        budget=FILE_OR_WEB,
    )

    rows = list(
        csv.DictReader(io.StringIO((await person.downloads(path, in_pod=pod)).decode()))
    )
    assert {r.get("customer") for r in rows} == {o["customer"] for o in OPEN}, rows


@scenario("A formatted report is made from the pod's data while the person waits")
@proves("PS-AGENT-016")
@covers(
    "agent.conversation.create",
    "agent.conversation.message.append",
    "agent.conversation.get",
)
async def test_a_pdf_report_is_made(desk):
    person, pod, table = desk
    path = f"/me/reports/{table}.pdf"

    await _asks(
        person,
        pod,
        f"Make a one-page PDF report of the orders in {table} — counts by status and the "
        f"open ones listed — and save it to {path}.",
        budget=DOCUMENT,
    )

    assert (await person.downloads(path, in_pod=pod))[:5] == b"%PDF-"


@scenario("A Word document is made from a doc while the person waits")
@proves("PS-AGENT-016")
@covers(
    "agent.conversation.create",
    "agent.conversation.message.append",
    "agent.conversation.get",
)
async def test_a_word_document_is_made(desk):
    person, pod, table = desk
    path = f"/me/reports/{table}.docx"

    await _asks(
        person,
        pod,
        f"Put the orders in {table} into a one-page Word document, grouped by "
        f"status, at {path}.",
        budget=DOCUMENT,
    )

    # A .docx is a zip holding word/document.xml; anything else under that
    # name is not one, and the orders have to be in it, not just a title.
    with zipfile.ZipFile(io.BytesIO(await person.downloads(path, in_pod=pod))) as docx:
        body = docx.read("word/document.xml").decode("utf-8", "replace")
    missing = [o["customer"] for o in ORDERS if o["customer"] not in body]
    assert not missing, f"the Word document leaves out {missing}"


@scenario("A short research memo cites the sources it was drawn from")
@proves("PS-AGENT-016")
@covers(
    "agent.conversation.create",
    "agent.conversation.message.append",
    "agent.conversation.get",
)
async def test_a_research_memo(desk):
    person, pod, _ = desk
    path = f"/me/research/{a_name_for('memo')}.md"

    await _asks(
        person,
        pod,
        "Research how small businesses are adopting AI agents this year and write "
        f"a short memo with at least three cited sources to {path}.",
        budget=RESEARCH,
    )

    memo = (await person.downloads(path, in_pod=pod)).decode("utf-8", "replace")
    assert _links(memo) >= 3, memo[:1500]


@scenario("A quick look-up on the web is answered with its source")
@proves("PS-AGENT-016")
@covers(
    "agent.conversation.create",
    "agent.conversation.message.append",
    "agent.conversation.get",
)
async def test_a_web_lookup(desk):
    person, pod, _ = desk

    reply = await _asks(
        person,
        pod,
        "What's the latest stable Python release? One line, with the source link.",
        budget=FILE_OR_WEB,
    )

    assert re.search(r"3\.1\d", reply) and _links(reply), reply[-800:]


@scenario("Something a person asks to be remembered is kept, without a detour")
@proves("PS-AGENT-016")
@covers(
    "agent.conversation.create",
    "agent.conversation.message.append",
    "agent.conversation.get",
)
async def test_something_is_remembered(desk):
    person, pod, _ = desk
    marker = a_name_for("preference")

    await _asks(
        person,
        pod,
        f"Remember that my reporting code is {marker}.",
        budget=QUICK,
    )

    hits = await person.searches_files(marker, in_pod=pod)
    found = any(
        marker in str(hit) for hit in (hits.get("results") or hits.get("items") or [])
    )
    if not found:
        # A plain loop: `any()` cannot consume an `await` inside a generator.
        for path in await person.paths_in(pod, directory="/me"):
            if not path.endswith(".md"):
                continue
            content = await person.downloads(path, in_pod=pod)
            if marker in content.decode("utf-8", "replace"):
                found = True
                break
    assert found, f"{marker} is in no memory file"


async def _the_widget(person, pod, transcript: str) -> str:
    """The HTML of the widget the run displayed, read back from the pod."""
    shown = [
        m.get("tool_args") or {}
        for m in json.loads(transcript)
        if m.get("kind") == "TOOL_CALL" and m.get("tool_name") == "display_resource"
    ]
    widget = next((a for a in shown if str(a.get("type")).upper() == "WIDGET"), None)
    assert widget is not None, f"no widget was displayed:\n{transcript[-1500:]}"
    if widget.get("path"):
        return (await person.downloads(widget["path"], in_pod=pod)).decode()
    return str(widget.get("content") or "")


@scenario("A widget of the pod's data is shown while the person waits")
@proves("PS-AGENT-016")
@covers(
    "agent.conversation.create",
    "agent.conversation.message.append",
    "agent.conversation.get",
)
async def test_a_widget_is_shown(desk):
    person, pod, table = desk

    transcript = await _asks(
        person,
        pod,
        f"Show me units by status in {table} as a widget.",
        budget=QUICK,
    )

    widget = await _the_widget(person, pod, transcript)
    # Drawn on the page it is served in: the query and the drawing come from
    # `window.lemma`, not from a loader or a token block pasted into the widget.
    assert table in widget and "lemma." in widget, widget
    assert "lemma-client.js" not in widget and "--lemma-widget-bg:" not in widget, widget


@scenario("A widget in a shape of its own is shown while the person waits")
@proves("PS-AGENT-016")
@covers(
    "agent.conversation.create",
    "agent.conversation.message.append",
    "agent.conversation.get",
)
async def test_a_widget_of_its_own_design(desk):
    person, pod, table = desk

    transcript = await _asks(
        person,
        pod,
        f"Show the orders in {table} as a kanban board by status, one card per order.",
        budget=WIDGET_OF_ITS_OWN,
    )

    widget = await _the_widget(person, pod, transcript)
    # Live from the table, or the seven orders carried in the page: both show them.
    assert table in widget or all(o["customer"] in widget for o in ORDERS), widget
