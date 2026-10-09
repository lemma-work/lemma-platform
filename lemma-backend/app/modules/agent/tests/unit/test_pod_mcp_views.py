"""The table view an MCP host draws pod records and query results in.

Two halves. The server's: which tools name the view, and that the view is
served the way MCP Apps hosts require. The view's, run under node: its pure
model -- what a tool result means as a table, and the arguments it sends back
for the next page -- and its bridge, against a host the test plays. Drawing it
in a real host is verified by hand (docs/architecture/mcp-plugin.md).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastmcp import Client

from app.mcp_server import build_pod_mcp_server
from app.modules.agent.services.pod_mcp_service import as_mcp_tool
from app.modules.agent.services.pod_mcp_tool_policy import POD_TOOL_POLICIES
from app.modules.agent.services.pod_mcp_views import POD_MCP_VIEWS, TABLE_VIEW
from app.modules.agent.tools.dispatcher import ToolInfo

pytestmark = pytest.mark.unit


class _Page(HTMLParser):
    """The view's inline scripts, and anything it would load from elsewhere.

    A parser rather than a pattern: tag names arrive lowercased whatever the
    file spells them, and attributes arrive parsed.
    """

    def __init__(self, html: str) -> None:
        super().__init__()
        self.scripts: list[str] = []
        self.loads: list[str] = []
        self._in_script = False
        self.feed(html)
        self.close()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        named = dict(attrs)
        source = named.get("href") if tag == "link" else named.get("src")
        if source:
            self.loads.append(source)
        if tag == "script":
            self._in_script = True
            self.scripts.append("")

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._in_script = False

    def handle_data(self, data: str) -> None:
        if self._in_script:
            self.scripts[-1] += data


_HTML = (
    Path(__file__).resolve().parents[2] / "services" / "pod_mcp_table_view.html"
).read_text(encoding="utf-8")
_PAGE = _Page(_HTML)
_SCRIPTS = _PAGE.scripts
_MODEL_SCRIPT, _BRIDGE_SCRIPT = _SCRIPTS


def _node() -> str:
    """Required, not skipped: a skip here would hide exactly the drift these
    tests exist to catch. The repository needs Node for its frontends anyway,
    and CI's Ubuntu runner ships it."""
    node = shutil.which("node")
    assert node, "these tests run the view's script under node; install Node"
    return node


def _tool(name: str) -> ToolInfo:
    return ToolInfo(name=name, description="", input_schema={"type": "object"})


# --- The server's half --------------------------------------------------------


def test_records_and_query_results_name_the_table_view():
    for name in ("pod_get_records", "pod_query"):
        assert as_mcp_tool(_tool(name)).meta == {
            "lemma_tool_name": name,
            "ui": {"resourceUri": "ui://lemma/table"},
        }


def test_tools_without_a_view_name_none():
    """A host given a `resourceUri` draws that view for every call -- a file
    read rendered as an empty table would be worse than its text."""
    assert as_mcp_tool(_tool("pod_read_file")).meta == {
        "lemma_tool_name": "pod_read_file"
    }


def test_only_reading_tools_have_a_view():
    """The view pages by calling its tool again, which must never write."""
    for name, policy in POD_TOOL_POLICIES.items():
        if policy.view is not None:
            assert policy.annotations().read_only_hint is True, name


async def test_every_view_a_tool_names_is_served():
    """A tool pointing at a `ui://` URI the server cannot read is a broken card
    in every host that supports MCP Apps."""
    named = {p.view.uri for p in POD_TOOL_POLICIES.values() if p.view is not None}
    async with Client(build_pod_mcp_server()) as client:
        served = {str(resource.uri) for resource in await client.list_resources()}
    assert named <= served
    assert served == {view.uri for view in POD_MCP_VIEWS}


async def test_the_table_view_is_served_as_an_mcp_app_that_reaches_nothing():
    async with Client(build_pod_mcp_server()) as client:
        [content] = await client.read_resource(TABLE_VIEW.uri)
    assert content.mime_type == "text/html;profile=mcp-app"
    assert content.meta == {
        "ui": {
            "csp": {"connectDomains": [], "resourceDomains": []},
            "prefersBorder": True,
        }
    }
    assert content.text.startswith("<!doctype html>")


def test_the_view_loads_nothing_from_anywhere():
    """The declared CSP is empty; a CDN script or a fetch would be blocked by
    every host, so the view must not depend on one."""
    assert _PAGE.loads == []
    assert "fetch(" not in _HTML
    assert "XMLHttpRequest" not in _HTML


def test_the_view_writes_pod_data_as_text_only():
    """Rows are whatever anyone with write access put in the table. Drawn as
    markup, a cell could run script in the person's conversation."""
    assert "innerHTML" not in _HTML
    assert "insertAdjacentHTML" not in _HTML
    assert "document.write" not in _HTML


# --- The view's half, under node -----------------------------------------------


def _run(harness: str) -> object:
    result = subprocess.run(
        [_node(), "-e", f"{_MODEL_SCRIPT}\n;console.log(JSON.stringify(({harness})));"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_both_scripts_parse(tmp_path):
    for index, script in enumerate(_SCRIPTS):
        path = tmp_path / f"script_{index}.js"
        path.write_text(script, encoding="utf-8")
        checked = subprocess.run(
            [_node(), "--check", str(path)], capture_output=True, text=True, check=False
        )
        assert checked.returncode == 0, checked.stderr


def _records_result(rows: list[dict[str, object]], total: int) -> str:
    return json.dumps(
        {"structuredContent": {"success": True, "records": rows, "total": total}}
    )


def test_a_page_of_records_knows_where_it_is_and_asks_for_the_next():
    rows = [{"id": str(i), "amount": i} for i in range(20)]
    seen = _run(
        f"""(() => {{
          const args = {{ table_name: "orders", sorts: [{{ column: "amount", direction: "desc" }}] }};
          const view = LemmaTable.model({_records_result(rows, 45)}, args);
          return {{
            kind: view.kind, table: view.table, columns: view.columns,
            summary: LemmaTable.summary(view), sort: view.sort,
            mode: LemmaTable.sortMode(view, true), modeWithoutTools: LemmaTable.sortMode(view, false),
            previous: LemmaTable.canPage(view, "previous"), next: LemmaTable.canPage(view, "next"),
            nextArgs: LemmaTable.pageArgs(args, view, "next"),
          }};
        }})()"""
    )
    assert seen == {
        "kind": "records",
        "table": "orders",
        "columns": ["amount", "id"],
        "summary": "1–20 of 45",
        "sort": {"column": "amount", "direction": "desc"},
        # Part of the table: re-sorting the page alone would misrepresent it.
        "mode": "server",
        "modeWithoutTools": "none",
        "previous": False,
        "next": True,
        "nextArgs": {
            "table_name": "orders",
            "sorts": [{"column": "amount", "direction": "desc"}],
            "offset": 20,
            "limit": 20,
        },
    }


def test_arguments_wrapped_under_the_request_stay_wrapped():
    rows = [{"id": "a"}, {"id": "b"}]
    seen = _run(
        f"""(() => {{
          const args = {{ request: {{ table_name: "orders", offset: 2, limit: 2 }} }};
          const view = LemmaTable.model({_records_result(rows, 6)}, args);
          return {{
            summary: LemmaTable.summary(view),
            previous: LemmaTable.pageArgs(args, view, "previous"),
            sorted: LemmaTable.sortArgs(args, view, "id", "asc"),
          }};
        }})()"""
    )
    assert seen == {
        "summary": "3–4 of 6",
        "previous": {"request": {"table_name": "orders", "offset": 0, "limit": 2}},
        "sorted": {
            "request": {
                "table_name": "orders",
                "offset": 0,
                "limit": 2,
                "sorts": [{"column": "id", "direction": "asc"}],
            }
        },
    }


def test_a_whole_table_sorts_where_it_is_with_empty_cells_last():
    rows = [{"n": 2}, {"n": None}, {"n": 10}, {"n": 1}]
    seen = _run(
        f"""(() => {{
          const view = LemmaTable.model({_records_result(rows, 4)}, {{ table_name: "t" }});
          return {{
            summary: LemmaTable.summary(view), mode: LemmaTable.sortMode(view, true),
            up: LemmaTable.sortRows(view.rows, "n", "asc").map((r) => r.n),
            down: LemmaTable.sortRows(view.rows, "n", "desc").map((r) => r.n),
          }};
        }})()"""
    )
    assert seen == {
        "summary": "4 records",
        "mode": "local",
        "up": [1, 2, 10, None],
        "down": [10, 2, 1, None],
    }


def test_a_query_cut_short_says_so_and_is_not_sorted_as_if_whole():
    result = {
        "structuredContent": {
            "success": True,
            "rows": [{"region": "EU", "total": 3}, {"region": "US", "total": 5}],
            "row_count": 2,
            "truncated": True,
        }
    }
    seen = _run(
        f"""(() => {{
          const view = LemmaTable.model({json.dumps(result)}, {{ sql: "select region, total from t" }});
          return {{ kind: view.kind, sql: view.sql, summary: LemmaTable.summary(view),
                   mode: LemmaTable.sortMode(view, true), next: LemmaTable.canPage(view, "next") }};
        }})()"""
    )
    assert seen == {
        "kind": "query",
        "sql": "select region, total from t",
        "summary": "First 2 rows — the result was cut short",
        "mode": "none",
        "next": False,
    }


@pytest.mark.parametrize(
    ("result", "message"),
    [
        (
            {
                "isError": True,
                "structuredContent": {
                    "success": False,
                    "error": "No table named orders",
                },
            },
            "No table named orders",
        ),
        ({"structuredContent": {"success": False}}, "The tool reported a failure."),
        (
            {"content": [{"type": "text", "text": "not json"}]},
            "This result has nothing a table can show.",
        ),
    ],
)
def test_a_failure_is_shown_as_one(result, message):
    seen = _run(f"LemmaTable.model({json.dumps(result)}, {{}})")
    assert seen == {"kind": "error", "message": message}


def test_a_host_that_forwards_only_text_still_gets_a_table():
    payload = {"success": True, "rows": [{"a": 1}], "row_count": 1, "truncated": False}
    result = {"content": [{"type": "text", "text": json.dumps(payload)}]}
    seen = _run(f"LemmaTable.summary(LemmaTable.model({json.dumps(result)}, {{}}))")
    assert seen == "1 row"


def test_one_record_is_shown_as_its_fields():
    result = {
        "structuredContent": {"success": True, "record": {"id": "r1", "name": "Ada"}}
    }
    seen = _run(
        f"LemmaTable.model({json.dumps(result)}, {{ table_name: 'people', record_id: 'r1' }})"
    )
    assert seen == {
        "kind": "record",
        "table": "people",
        "id": "r1",
        "fields": [["name", "Ada"], ["id", "r1"]],
    }


def test_cells_are_shown_as_a_person_reads_them():
    seen = _run(
        """[null, true, 12.5, "2026-10-06T14:03:00Z", "2026-10-06 15:42:29.453694+00:00",
            "2026-10-06T14:03", "2026-10-06", {"a": [1]}, []]
             .map((value) => LemmaTable.cell(value, { locale: "en-US", timeZone: "UTC" }))"""
    )
    for cell in seen:
        # ICU 72 and later put a narrow no-break space before "PM".
        cell["text"] = cell["text"].replace("\u202f", " ")
    assert seen == [
        {"kind": "empty", "text": "\u2014", "label": "empty"},
        {"kind": "bool", "text": "Yes"},
        {"kind": "number", "text": "12.5"},
        # Written for the person, with the stored value one hover away.
        {
            "kind": "date",
            "text": "Oct 6, 2026, 2:03 PM",
            "title": "2026-10-06T14:03:00Z",
        },
        # As the datastore returns one, which Safari's Date will not parse as is.
        {
            "kind": "date",
            "text": "Oct 6, 2026, 3:42 PM",
            "title": "2026-10-06 15:42:29.453694+00:00",
        },
        # No zone: a wall-clock time somewhere unknown, so not converted.
        {"kind": "text", "text": "2026-10-06T14:03"},
        # A bare date has no zone to move it across midnight; shown as stored.
        {"kind": "text", "text": "2026-10-06"},
        {"kind": "json", "text": '{"a":[1]}'},
        # An empty list reads as nothing, the way an empty cell does.
        {"kind": "empty", "text": "\u2014", "label": "empty"},
    ]


def test_a_tables_own_columns_come_before_the_ones_every_table_has():
    record = {
        "id": "r1",
        "created_at": "x",
        "customer": "Ada",
        "user_id": "u",
        "amount": 3,
    }
    query = {
        "structuredContent": {"success": True, "rows": [record], "truncated": False}
    }
    seen = _run(
        f"""[LemmaTable.model({_records_result([record], 1)}, {{}}).columns,
            LemmaTable.model({json.dumps(query)}, {{}}).columns]"""
    )
    assert seen == [
        ["customer", "amount", "id", "created_at", "user_id"],
        # A query's columns are in the order its SELECT chose.
        ["id", "created_at", "customer", "user_id", "amount"],
    ]


def test_the_views_system_columns_are_the_datastores():
    """Kept by hand in the view's script, so checked against the source."""
    from app.modules.datastore.domain.datastore_entities import SYSTEM_COLUMNS

    listed = re.search(r"const SYSTEM_COLUMNS = (\[[^\]]*\]);", _MODEL_SCRIPT)
    assert listed is not None
    assert set(json.loads(listed.group(1))) == SYSTEM_COLUMNS | {"id"}


def test_the_model_is_told_which_page_the_person_moved_to():
    rows = [{"id": "x"}]
    seen = _run(
        f"""(() => {{
          const args = {{ table_name: "orders", offset: 20, limit: 1, sorts: [{{ column: "id", direction: "desc" }}] }};
          return LemmaTable.contextFor(LemmaTable.model({_records_result(rows, 40)}, args));
        }})()"""
    )
    assert seen == {
        "content": [
            {
                "type": "text",
                "text": (
                    'The person is looking at records 21 of 40 in the Lemma pod table "orders", '
                    "sorted by id descending.\n"
                    'The rows shown:\n[{"id":"x"}]'
                ),
            }
        ]
    }


def test_a_page_too_big_to_hand_the_model_is_summarised():
    rows = [{"text": "x" * 1000} for _ in range(40)]
    seen = _run(
        f"""LemmaTable.contextFor(LemmaTable.model({_records_result(rows, 40)}, {{ table_name: "notes" }}))
             .content[0].text"""
    )
    assert (
        seen
        == 'The person is looking at all 40 records in the Lemma pod table "notes".'
    )


def test_a_numeric_string_is_lined_up_and_sorted_as_a_number():
    """Postgres `numeric` reaches JSON as a string so no digit is lost; sorted
    as text, 9.75 would land after 9.8."""
    seen = _run(
        """({
          cell: LemmaTable.cell("1867.60"),
          zip: LemmaTable.cell("02134"),
          sorted: LemmaTable.sortRows([{ v: "9.8" }, { v: "10" }, { v: "9.75" }, { v: "-1" }], "v", "asc")
            .map((row) => row.v),
        })"""
    )
    assert seen == {
        "cell": {"kind": "number", "text": "1867.60"},
        # Shown exactly as stored: nothing is reformatted.
        "zip": {"kind": "number", "text": "02134"},
        "sorted": ["-1", "9.75", "9.8", "10"],
    }


# --- The bridge, against a host played by the test ------------------------------

# Just enough DOM for the bridge to run: elements remember what is set on them
# and which listeners they were given; anything else answers with a stand-in.
_FAKE_PAGE = r"""
const absorb = () => new Proxy(function () {}, {
  get: (_, key) => (key === Symbol.toPrimitive ? () => "" : absorb()),
  set: () => true, apply: () => absorb(), construct: () => absorb(),
});
const elements = {};
function element(id) {
  if (elements[id]) return elements[id];
  const state = { listeners: {}, addEventListener(type, fn) { state.listeners[type] = fn; } };
  return (elements[id] = new Proxy(state, {
    get: (target, key) => (key in target ? target[key] : key === Symbol.toPrimitive ? () => "" : absorb()),
    set: (target, key, value) => { target[key] = value; return true; },
  }));
}
const posted = [];
let deliver = null;
const parent = { postMessage: (message) => posted.push(JSON.parse(JSON.stringify(message))) };
globalThis.window = { parent, addEventListener: (type, fn) => { if (type === "message") deliver = fn; } };
globalThis.document = {
  getElementById: element, documentElement: element(":root"), head: absorb(),
  createElement: () => absorb(), createDocumentFragment: () => absorb(),
};
globalThis.ResizeObserver = class { observe() {} };
globalThis.requestAnimationFrame = () => 0;
globalThis.cancelAnimationFrame = () => {};
const fromHost = (data) => deliver({ source: parent, data: { jsonrpc: "2.0", ...data } });
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));
"""


def test_the_bridge_shakes_hands_draws_pages_and_tells_the_model():
    first = _records_result([{"id": str(i)} for i in range(20)], 57)
    second = _records_result([{"id": str(i)} for i in range(20, 40)], 57)
    harness = f"""
      (async () => {{
        const init = posted[0];
        fromHost({{ id: init.id, result: {{
          protocolVersion: "2026-01-26", hostCapabilities: {{ serverTools: {{}} }},
          hostContext: {{ toolInfo: {{ tool: {{ name: "lemma_pod_get_records" }} }}, displayMode: "inline" }},
        }} }});
        await settle();
        const initialized = posted[1];
        fromHost({{ method: "ui/notifications/tool-input", params: {{ arguments: {{ table_name: "orders" }} }} }});
        fromHost({{ method: "ui/notifications/tool-result", params: {first} }});
        const drawn = {{ title: elements.title.textContent, count: elements.count.textContent,
                        pager: elements.pager.hidden, next: elements.next.disabled }};
        elements.next.listeners.click();
        await settle();
        const call = posted[2];
        fromHost({{ id: call.id, result: {second} }});
        await settle();
        const paged = {{ count: elements.count.textContent, previous: elements.prev.disabled }};
        const context = posted[3];
        fromHost({{ id: 41, method: "ui/resource-teardown", params: {{ reason: "closed" }} }});
        fromHost({{ id: 42, method: "tools/list", params: {{}} }});
        // A message from anywhere but the host is not listened to.
        deliver({{ source: {{}}, data: {{ jsonrpc: "2.0", id: 43, method: "ping" }} }});
        console.log(JSON.stringify({{ init, initialized, drawn, call, paged,
          context: {{ method: context.method }}, replies: posted.slice(4) }}));
        // The context request is never answered here; its timer would hold node open.
        process.exit(0);
      }})();
    """
    result = subprocess.run(
        [_node(), "-e", f"{_FAKE_PAGE}\n{_MODEL_SCRIPT}\n{_BRIDGE_SCRIPT}\n{harness}"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    seen = json.loads(result.stdout)
    # `appInfo`, as the spec's types say and SDK-built hosts validate: the
    # prose example's `clientInfo` is answered with an error, and the card
    # never draws. That is what the first run in a real host showed.
    assert seen["init"]["method"] == "ui/initialize"
    assert set(seen["init"]["params"]) == {
        "protocolVersion",
        "appInfo",
        "appCapabilities",
    }
    assert seen["init"]["params"]["protocolVersion"] == "2026-01-26"
    assert seen["initialized"] == {
        "jsonrpc": "2.0",
        "method": "ui/notifications/initialized",
        "params": {},
    }
    assert seen["drawn"] == {
        "title": "orders",
        "count": "1–20 of 57",
        "pager": False,
        "next": False,
    }
    assert seen["call"]["method"] == "tools/call"
    assert seen["call"]["params"] == {
        "name": "lemma_pod_get_records",
        "arguments": {"table_name": "orders", "offset": 20, "limit": 20},
    }
    assert seen["paged"] == {"count": "21–40 of 57", "previous": False}
    assert seen["context"] == {"method": "ui/update-model-context"}
    assert seen["replies"] == [
        {"jsonrpc": "2.0", "id": 41, "result": {}},
        {
            "jsonrpc": "2.0",
            "id": 42,
            "error": {"code": -32601, "message": "Method not found: tools/list"},
        },
    ]


def test_a_page_that_answers_after_a_newer_result_is_dropped():
    """The person pages, and before the page arrives the model calls the tool
    again. The late page belongs to the old result; drawn, it would replace
    the new one and tell the model about rows no longer on screen."""
    first = _records_result([{"id": str(i)} for i in range(20)], 57)
    newer = _records_result([{"name": "Ada"}, {"name": "Grace"}], 2)
    late = _records_result([{"id": str(i)} for i in range(20, 40)], 57)
    harness = f"""
      (async () => {{
        fromHost({{ id: posted[0].id, result: {{
          protocolVersion: "2026-01-26", hostCapabilities: {{ serverTools: {{}} }},
          hostContext: {{ toolInfo: {{ tool: {{ name: "lemma_pod_get_records" }} }} }},
        }} }});
        await settle();
        fromHost({{ method: "ui/notifications/tool-input", params: {{ arguments: {{ table_name: "orders" }} }} }});
        fromHost({{ method: "ui/notifications/tool-result", params: {first} }});
        elements.next.listeners.click();
        await settle();
        const call = posted.find((m) => m.method === "tools/call");
        fromHost({{ method: "ui/notifications/tool-input", params: {{ arguments: {{ table_name: "people" }} }} }});
        fromHost({{ method: "ui/notifications/tool-result", params: {newer} }});
        fromHost({{ id: call.id, result: {late} }});
        await settle();
        console.log(JSON.stringify({{
          title: elements.title.textContent, count: elements.count.textContent,
          told: posted.filter((m) => m.method === "ui/update-model-context").length,
        }}));
        process.exit(0);
      }})();
    """
    result = subprocess.run(
        [_node(), "-e", f"{_FAKE_PAGE}\n{_MODEL_SCRIPT}\n{_BRIDGE_SCRIPT}\n{harness}"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "title": "people",
        "count": "2 records",
        "told": 0,
    }
