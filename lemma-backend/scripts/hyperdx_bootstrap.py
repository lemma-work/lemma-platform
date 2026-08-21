"""Bootstrap a local HyperDX (ClickStack) team and print its ingestion API key.

HyperDX's all-in-one image runs in REQUIRED_AUTH mode: nothing can send or
view telemetry until a team exists. This registers a fixed local-dev team on
first run and logs into that same team on every run after (the Mongo volume
persists it across restarts), then reads the team's OTLP ingestion API key
from `GET /api/team`. Prints only the key to stdout so a Makefile recipe can
capture it directly; everything else goes to stderr.

It also provisions (once, idempotently) a default "Lemma API Overview"
dashboard covering the auto-created Traces and Logs sources, and prints the
user's Personal API Access Key (`GET /api/me`) as a ready-to-export
`CLICKSTACK_ACCESS_KEY` line — the credential the ClickStack MCP server
(`/api/mcp`, see `.mcp.json` and `docs/observability.md`) authenticates with.
"""

from __future__ import annotations

import argparse
from http.cookies import SimpleCookie
import sys

import httpx

_EMAIL = "dev@lemma.local"
# HyperDX requires >=12 chars, upper+lower+digit+special. Local-only, never
# used for anything but this throwaway dev team.
_PASSWORD = "Lemma-Dev-Observability-1!"

_DASHBOARD_NAME = "Lemma API Overview"
_SERVER = 'SpanKind:"Server"'
_ERROR = 'StatusCode:"Error"'
_DURATION_FORMAT = {"output": "duration", "factor": 0.000000001}


def _select(agg_fn, *, alias, value_expression="", level=None, condition=""):
    item = {
        "aggFn": agg_fn,
        "aggCondition": condition,
        "aggConditionLanguage": "lucene",
        "valueExpression": value_expression,
        "alias": alias,
        "isDelta": False,
    }
    if level is not None:
        item["level"] = level
        item["numberFormat"] = _DURATION_FORMAT
    return item


def _quantile(level, alias, *, condition=_SERVER):
    return _select(
        "quantile",
        alias=alias,
        value_expression="Duration",
        level=level,
        condition=condition,
    )


def _overview_dashboard(
    traces_source_id: str, logs_source_id: str, connection_id: str
) -> dict:
    """Tile shapes are the exact structures HyperDX persisted after being
    authored and validated (via `clickstack_save_dashboard` +
    `clickstack_query_tile`, one tile at a time) through the ClickStack MCP
    server against a live instance — not hand-guessed against the schema.
    Two things learned that aren't obvious from the schema alone:

    - A select item's conditional filter must be `aggCondition` +
      `aggConditionLanguage` (not `where`), or the condition silently fails
      at render time despite passing save-time validation.
    - Builder `table` tiles combining a top-level `where` with a
      map-attribute `groupBy` (e.g. ``SpanAttributes['http.route']``) silently
      ignore the `where` filter. Raw SQL tiles (`configType: "sql"`) don't
      have this problem and also get a clean column alias instead of the
      literal `arrayElement(SpanAttributes, 'http.route')` header a builder
      groupBy on a map attribute would render.
    """
    return {
        "name": _DASHBOARD_NAME,
        "tags": [],
        "containers": [
            {"id": "overview", "title": "Overview", "collapsed": False},
            {"id": "performance", "title": "Performance", "collapsed": False},
            {"id": "endpoints", "title": "Endpoints", "collapsed": False},
            {"id": "errors", "title": "Errors", "collapsed": False},
            {"id": "logs", "title": "Logs", "collapsed": False},
        ],
        "tiles": [
            {
                "id": "requests",
                "x": 0,
                "y": 0,
                "w": 8,
                "h": 4,
                "containerId": "overview",
                "config": {
                    "name": "Requests",
                    "displayType": "number",
                    "source": traces_source_id,
                    "where": "",
                    "select": [_select("count", alias="Requests", condition=_SERVER)],
                },
            },
            {
                "id": "errors",
                "x": 8,
                "y": 0,
                "w": 8,
                "h": 4,
                "containerId": "overview",
                "config": {
                    "name": "Errors",
                    "displayType": "number",
                    "source": traces_source_id,
                    "where": "",
                    "select": [
                        _select(
                            "count",
                            alias="Errors",
                            condition=f"({_SERVER}) AND ({_ERROR})",
                        )
                    ],
                },
            },
            {
                "id": "p95-duration",
                "x": 16,
                "y": 0,
                "w": 8,
                "h": 4,
                "containerId": "overview",
                "config": {
                    "name": "P95 Duration",
                    "displayType": "number",
                    "source": traces_source_id,
                    "where": "",
                    "select": [_quantile(0.95, "P95")],
                },
            },
            {
                "id": "latency-percentiles",
                "x": 0,
                "y": 4,
                "w": 12,
                "h": 6,
                "containerId": "performance",
                "config": {
                    "name": "Latency Percentiles",
                    "displayType": "line",
                    "source": traces_source_id,
                    "where": "",
                    "select": [
                        _quantile(0.5, "P50"),
                        _quantile(0.95, "P95"),
                        _quantile(0.99, "P99"),
                    ],
                },
            },
            {
                "id": "duration-distribution",
                "x": 12,
                "y": 4,
                "w": 12,
                "h": 6,
                "containerId": "performance",
                "config": {
                    "name": "Duration Distribution",
                    "displayType": "heatmap",
                    "source": traces_source_id,
                    "where": _SERVER,
                    "whereLanguage": "lucene",
                    "numberFormat": _DURATION_FORMAT,
                    "select": [_select("count", alias="", value_expression="Duration")],
                },
            },
            {
                "id": "endpoints",
                "x": 0,
                "y": 10,
                "w": 24,
                "h": 8,
                "containerId": "endpoints",
                "config": {
                    "name": "Endpoints",
                    "configType": "sql",
                    "displayType": "table",
                    "connection": connection_id,
                    "source": traces_source_id,
                    "sqlTemplate": (
                        "SELECT SpanAttributes['http.route'] AS Endpoint, "
                        "count() AS Requests, "
                        "countIf(StatusCode = 'Error') AS Errors, "
                        "quantile(0.95)(Duration / 1e9) AS P95Duration "
                        "FROM $__sourceTable "
                        "WHERE SpanKind = 'Server' AND SpanAttributes['http.route'] != '' "
                        "AND $__timeFilter(Timestamp) AND $__filters "
                        "GROUP BY Endpoint ORDER BY Requests DESC"
                    ),
                },
            },
            {
                "id": "errors-by-kind",
                "x": 0,
                "y": 18,
                "w": 12,
                "h": 6,
                "containerId": "errors",
                "config": {
                    "name": "Errors over time (by kind)",
                    "displayType": "stacked_bar",
                    "source": traces_source_id,
                    "where": "",
                    "groupBy": "SpanKind",
                    "select": [_select("count", alias="Errors", condition=_ERROR)],
                },
            },
            {
                "id": "errors-by-status",
                "x": 12,
                "y": 18,
                "w": 12,
                "h": 6,
                "containerId": "errors",
                "config": {
                    "name": "Errors by HTTP status code",
                    "configType": "sql",
                    "displayType": "table",
                    "connection": connection_id,
                    "source": traces_source_id,
                    "sqlTemplate": (
                        "SELECT SpanAttributes['http.status_code'] AS StatusCode_, "
                        "count() AS Count "
                        "FROM $__sourceTable "
                        "WHERE StatusCode = 'Error' AND $__timeFilter(Timestamp) AND $__filters "
                        "GROUP BY StatusCode_ ORDER BY Count DESC"
                    ),
                },
            },
            {
                "id": "recent-error-spans",
                "x": 0,
                "y": 24,
                "w": 24,
                "h": 6,
                "containerId": "errors",
                "config": {
                    "name": "Recent error spans",
                    "displayType": "search",
                    "source": traces_source_id,
                    "where": _ERROR,
                    "whereLanguage": "lucene",
                    "select": (
                        "Timestamp, ServiceName, SpanKind, SpanName, "
                        "SpanAttributes['http.status_code'], "
                        "SpanAttributes['http.route'], round(Duration/1e6)"
                    ),
                },
            },
            {
                "id": "logs-by-severity",
                "x": 0,
                "y": 30,
                "w": 12,
                "h": 6,
                "containerId": "logs",
                "config": {
                    "name": "Volume by severity",
                    "displayType": "stacked_bar",
                    "source": logs_source_id,
                    "where": "",
                    "groupBy": "SeverityText",
                    "select": [_select("count", alias="Events")],
                },
            },
            {
                "id": "recent-warnings-errors",
                "x": 12,
                "y": 30,
                "w": 12,
                "h": 6,
                "containerId": "logs",
                "config": {
                    "name": "Recent warnings & errors",
                    "displayType": "search",
                    "source": logs_source_id,
                    "where": "SeverityText:warn OR SeverityText:error",
                    "whereLanguage": "lucene",
                    "select": "Timestamp, ServiceName, SeverityText, Body",
                },
            },
        ],
    }


def _session_cookie(response: httpx.Response) -> str | None:
    # HyperDX's session cookie is scoped to `Domain=localhost` (no dot), which
    # httpx's cookie jar (backed by stdlib http.cookiejar) silently refuses to
    # attach to later requests. Parse and carry it manually instead.
    raw = response.headers.get("set-cookie")
    if not raw:
        return None
    parsed: SimpleCookie = SimpleCookie()
    parsed.load(raw)
    morsel = parsed.get("connect.sid")
    return morsel.value if morsel else None


def _register_or_login(client: httpx.Client) -> str:
    register = client.post(
        "/api/register/password",
        json={"email": _EMAIL, "password": _PASSWORD, "confirmPassword": _PASSWORD},
    )
    if register.status_code == 200:
        cookie = _session_cookie(register)
        if cookie:
            return cookie
        raise RuntimeError("HyperDX registration succeeded but set no session cookie")
    if register.status_code != 409:
        raise RuntimeError(
            f"HyperDX registration failed: {register.status_code} {register.text}"
        )
    # 409 means a team already exists from a previous run — log into it
    # instead. HyperDX's local strategy authenticates on `email`, not
    # `username`.
    login = client.post(
        "/api/login/password",
        json={"email": _EMAIL, "password": _PASSWORD},
    )
    if login.status_code not in (200, 303):
        raise RuntimeError(f"HyperDX login failed: {login.status_code} {login.text}")
    cookie = _session_cookie(login)
    if not cookie:
        raise RuntimeError("HyperDX login succeeded but set no session cookie")
    return cookie


def _ensure_dashboard(client: httpx.Client) -> None:
    dashboards = client.get("/api/dashboards")
    dashboards.raise_for_status()
    if any(d.get("name") == _DASHBOARD_NAME for d in dashboards.json()):
        return  # already provisioned by a previous run

    sources = client.get("/api/sources")
    sources.raise_for_status()
    by_kind = {s.get("kind"): s for s in sources.json()}
    traces_source = by_kind.get("trace")
    logs_source = by_kind.get("log")
    if traces_source is None or logs_source is None:
        raise RuntimeError("HyperDX is missing a default Traces or Logs source")

    created = client.post(
        "/api/dashboards",
        json=_overview_dashboard(
            traces_source["_id"], logs_source["_id"], traces_source["connection"]
        ),
    )
    created.raise_for_status()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()

    try:
        with httpx.Client(
            base_url=args.base_url, timeout=args.timeout, follow_redirects=False
        ) as client:
            cookie = _register_or_login(client)
            client.headers["Cookie"] = f"connect.sid={cookie}"
            team = client.get("/api/team")
            team.raise_for_status()
            api_key = team.json().get("apiKey")
            me = client.get("/api/me")
            me.raise_for_status()
            personal_access_key = me.json().get("accessKey")
            _ensure_dashboard(client)
    except httpx.HTTPError as exc:
        print(
            f"hyperdx_bootstrap: request to {args.base_url} failed: {exc}",
            file=sys.stderr,
        )
        return 1
    except RuntimeError as exc:
        print(f"hyperdx_bootstrap: {exc}", file=sys.stderr)
        return 1

    if not api_key:
        print(
            f"hyperdx_bootstrap: team response had no apiKey: {team.text}",
            file=sys.stderr,
        )
        return 1
    if personal_access_key:
        print(
            f"export CLICKSTACK_ACCESS_KEY={personal_access_key}  "
            "# for the ClickStack MCP server (.mcp.json) — see docs/observability.md",
            file=sys.stderr,
        )

    print(api_key)
    return 0


if __name__ == "__main__":
    sys.exit(main())
