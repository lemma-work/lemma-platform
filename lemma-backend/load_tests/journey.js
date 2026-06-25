/**
 * Lemma full-journey load test.
 *
 * Each virtual user runs the real chat journey end-to-end and we measure the
 * latency of every API in the flow plus the full message round-trip (agent run
 * through the worker + SSE). The LLM is the deterministic in-process mock
 * (E2E_LLM_MODE=mock on the api+worker containers); the agent streams text and
 * a real `write_todos` tool call scripted via the conversation metadata, so the
 * journey exercises api + worker + redis + DB + SSE with no real model.
 *
 * Profile (sustained loop):
 *   - first iteration per VU = provision: signup -> org -> pod -> TODO agent ->
 *     conversation (carrying the mock_llm_script).
 *   - every iteration = send the same message and read the SSE stream to
 *     completion, then think-time sleep.
 *
 * Required env (LEMMA_API_URL defaults to host gateway). No setup.py needed —
 * each VU provisions its own user.
 *
 * Run (see `make load-test-journey`):
 *   docker run --rm --network host -e LEMMA_API_URL=http://localhost:8000 \
 *     -e MAX_USERS=100 -v ./load_tests:/scripts \
 *     grafana/k6:latest run /scripts/journey.js
 */

import { check, sleep } from "k6";
import http from "k6/http";
import { Counter, Rate, Trend } from "k6/metrics";

// --------------------------------------------------------------------------
// Config
// --------------------------------------------------------------------------

const API_URL = __ENV.LEMMA_API_URL || "http://localhost:8000";
const MAX_USERS = parseInt(__ENV.MAX_USERS || "100", 10);
const THINK_MS = parseInt(__ENV.THINK_MS || "500", 10);
const RAMP = __ENV.RAMP || "2m30s";
const HOLD = __ENV.HOLD || "2m";
const SUMMARY_PATH = __ENV.SUMMARY_PATH || "/scripts/journey_summary.json";
const PASSWORD = "LoadTest@12345";
const MESSAGE = "Please record my todos and confirm.";

// Two model turns: turn 0 streams text + a write_todos tool call; after the tool
// return, turn 1 streams the final text. Turn index resets per run, so the same
// script drives every repeated message.
const MOCK_LLM_SCRIPT = [
  {
    text: "On it — recording your tasks now.",
    tool_calls: [
      {
        tool_name: "write_todos",
        args: { todos: ["- [ ] handle request", "- [x] done"] },
      },
    ],
  },
  { text: "All set — your todos are updated." },
];

// --------------------------------------------------------------------------
// Custom metrics — one Trend per API for avg + p50/p90/p95/p99
// --------------------------------------------------------------------------

const signupMs = new Trend("signup_ms", true);
const createOrgMs = new Trend("create_org_ms", true);
const createPodMs = new Trend("create_pod_ms", true);
const createAgentMs = new Trend("create_agent_ms", true);
const createConvMs = new Trend("create_conv_ms", true);
const messageRoundtripMs = new Trend("message_roundtrip_ms", true);

const provisionSuccess = new Rate("provision_success");
const messageSuccess = new Rate("message_success");
const tokensReceived = new Counter("tokens_received");
const toolCallsReceived = new Counter("tool_calls_received");
const journeyErrors = new Counter("journey_errors");

// --------------------------------------------------------------------------
// Scenario
// --------------------------------------------------------------------------

export const options = {
  scenarios: {
    journey: {
      executor: "ramping-vus",
      exec: "journeyVU",
      startVUs: 0,
      stages: [
        { duration: RAMP, target: MAX_USERS },
        { duration: HOLD, target: MAX_USERS },
        { duration: "30s", target: 0 },
      ],
      gracefulRampDown: "10s",
    },
  },
  // Report avg + p50/p90/p95/p99 (+ min/max/count) for every Trend.
  summaryTrendStats: ["avg", "min", "med", "p(90)", "p(95)", "p(99)", "max", "count"],
  thresholds: {
    journey_errors: ["count<100"],
    provision_success: ["rate>0.95"],
    message_success: ["rate>0.95"],
  },
};

// --------------------------------------------------------------------------
// Helpers
// --------------------------------------------------------------------------

function authHeaders(token) {
  return { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
}

function extractAccessToken(resp) {
  const h = resp.headers || {};
  const fromHeader =
    h["St-Access-Token"] || h["st-access-token"] || h["ST-ACCESS-TOKEN"] || "";
  if (fromHeader) return fromHeader;
  // Fallback: SuperTokens may deliver the token as a cookie.
  const c = resp.cookies || {};
  if (c["sAccessToken"] && c["sAccessToken"].length) {
    return c["sAccessToken"][0].value || "";
  }
  return "";
}

function countOccurrences(haystack, needle) {
  if (!haystack) return 0;
  return haystack.split(needle).length - 1;
}

// Per-VU state (module scope is per-VU in k6, shared across that VU's iterations).
let vuState = null;

// --------------------------------------------------------------------------
// Provision: signup -> org -> pod -> agent -> conversation
// --------------------------------------------------------------------------

function provision() {
  const email = `loadtest+${__VU}-${Date.now()}@example.com`;

  let resp = http.post(
    `${API_URL}/st/auth/signup`,
    JSON.stringify({
      formFields: [
        { id: "email", value: email },
        { id: "password", value: PASSWORD },
      ],
    }),
    { headers: { "Content-Type": "application/json" }, tags: { name: "signup" }, timeout: "30s" }
  );
  signupMs.add(resp.timings.duration);
  const token = extractAccessToken(resp);
  if (resp.status !== 200 || !token) {
    journeyErrors.add(1);
    provisionSuccess.add(false);
    return null;
  }
  const H = authHeaders(token);

  const uniq = `${__VU}-${Date.now()}`;
  resp = http.post(
    `${API_URL}/organizations`,
    JSON.stringify({ name: `LoadTest Org ${uniq}` }),
    { headers: H, tags: { name: "create_org" }, timeout: "30s" }
  );
  createOrgMs.add(resp.timings.duration);
  if (resp.status !== 201) {
    journeyErrors.add(1);
    provisionSuccess.add(false);
    return null;
  }
  const orgId = resp.json("id");

  resp = http.post(
    `${API_URL}/pods`,
    JSON.stringify({ organization_id: orgId, name: `LoadTest Pod ${uniq}` }),
    { headers: H, tags: { name: "create_pod" }, timeout: "30s" }
  );
  createPodMs.add(resp.timings.duration);
  if (resp.status !== 201) {
    journeyErrors.add(1);
    provisionSuccess.add(false);
    return null;
  }
  const podId = resp.json("id");

  const agentName = `todo-agent-${__VU}`;
  resp = http.post(
    `${API_URL}/pods/${podId}/agents`,
    JSON.stringify({
      name: agentName,
      instruction: "Record the user's todos with write_todos, then confirm briefly.",
      toolsets: ["TODO"],
    }),
    { headers: H, tags: { name: "create_agent" }, timeout: "30s" }
  );
  createAgentMs.add(resp.timings.duration);
  if (resp.status !== 201) {
    journeyErrors.add(1);
    provisionSuccess.add(false);
    return null;
  }
  const createdAgent = resp.json("name") || agentName;

  resp = http.post(
    `${API_URL}/pods/${podId}/conversations`,
    JSON.stringify({
      agent_name: createdAgent,
      title: `LoadTest Conv ${__VU}`,
      type: "CHAT",
      metadata: { mock_llm_script: MOCK_LLM_SCRIPT },
    }),
    { headers: H, tags: { name: "create_conv" }, timeout: "30s" }
  );
  createConvMs.add(resp.timings.duration);
  if (resp.status !== 201) {
    journeyErrors.add(1);
    provisionSuccess.add(false);
    return null;
  }
  const convId = resp.json("id");

  provisionSuccess.add(true);
  return { podId, convId, H };
}

// --------------------------------------------------------------------------
// VU: provision once, then send the message in a loop (sustained)
// --------------------------------------------------------------------------

export function journeyVU() {
  if (!vuState) {
    vuState = provision();
    if (!vuState) {
      sleep(1);
      return;
    }
  }

  // POST /messages returns text/event-stream and blocks until the run reaches a
  // terminal event; full duration = api->DB->redis->worker->mock->SSE round-trip.
  const resp = http.post(
    `${API_URL}/pods/${vuState.podId}/conversations/${vuState.convId}/messages`,
    JSON.stringify({ content: MESSAGE }),
    { headers: vuState.H, tags: { name: "message" }, timeout: "120s", responseType: "text" }
  );
  messageRoundtripMs.add(resp.timings.duration);

  const body = resp.body || "";
  const completed = resp.status === 200 && body.indexOf('"type": "completed"') !== -1;
  messageSuccess.add(completed);
  if (!completed) {
    journeyErrors.add(1);
  }

  const tokens = countOccurrences(body, '"type": "token"');
  if (tokens > 0) tokensReceived.add(tokens);
  if (body.indexOf("write_todos") !== -1) toolCallsReceived.add(1);

  check(resp, { "message run completed": () => completed });

  sleep(THINK_MS / 1000);
}

// --------------------------------------------------------------------------
// Summary: per-API latency table + JSON dump
// --------------------------------------------------------------------------

const LATENCY_METRICS = [
  ["signup", "signup_ms"],
  ["create_org", "create_org_ms"],
  ["create_pod", "create_pod_ms"],
  ["create_agent", "create_agent_ms"],
  ["create_conv", "create_conv_ms"],
  ["message_roundtrip", "message_roundtrip_ms"],
];

function fmt(n) {
  if (n === undefined || n === null || isNaN(n)) return "-";
  return n >= 100 ? n.toFixed(0) : n.toFixed(1);
}

function pad(s, w) {
  s = String(s);
  return s.length >= w ? s : s + " ".repeat(w - s.length);
}

export function handleSummary(data) {
  const m = data.metrics;
  const lines = [];
  lines.push("");
  lines.push("================ Per-API latency (ms) ================");
  lines.push(
    pad("api", 20) + pad("count", 8) + pad("avg", 9) + pad("p50", 9) +
    pad("p90", 9) + pad("p95", 9) + pad("p99", 9) + pad("max", 9)
  );
  for (const [label, key] of LATENCY_METRICS) {
    const v = (m[key] && m[key].values) || {};
    lines.push(
      pad(label, 20) +
      pad(fmt(v.count), 8) +
      pad(fmt(v.avg), 9) +
      pad(fmt(v.med), 9) +
      pad(fmt(v["p(90)"]), 9) +
      pad(fmt(v["p(95)"]), 9) +
      pad(fmt(v["p(99)"]), 9) +
      pad(fmt(v.max), 9)
    );
  }
  lines.push("------------------------------------------------------");
  const ms = (m.message_success && m.message_success.values) || {};
  const ps = (m.provision_success && m.provision_success.values) || {};
  lines.push("provision_success rate : " + fmt((ps.rate || 0) * 100) + "%");
  lines.push("message_success rate   : " + fmt((ms.rate || 0) * 100) + "%");
  lines.push("tokens_received        : " + fmt((m.tokens_received && m.tokens_received.values.count)));
  lines.push("tool_calls_received    : " + fmt((m.tool_calls_received && m.tool_calls_received.values.count)));
  lines.push("journey_errors         : " + fmt((m.journey_errors && m.journey_errors.values.count)));
  lines.push("======================================================");
  lines.push("");

  const out = {};
  out["stdout"] = lines.join("\n");
  out[SUMMARY_PATH] = JSON.stringify(data, null, 2);
  return out;
}
