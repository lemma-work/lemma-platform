/**
 * Lemma full-journey load test.
 *
 * Each virtual user runs the real product journey end-to-end and we measure the
 * latency of every API plus the full agent message round-trip. Everything runs
 * against mocks so no external service is needed:
 *   - LLM: deterministic in-process FunctionModel (E2E_LLM_MODE=mock).
 *   - Sandbox/AgentBox: the in-process fake AgentBox service (E2E_SANDBOX_MODE=
 *     fake, AGENTBOX_API_URL -> fake-agentbox) — so function create + execute run
 *     without a real Docker sandbox (functions echo their input).
 *
 * Per VU:
 *   - first iteration = provision: signup -> org -> pod -> TODO agent ->
 *     conversation (mock_llm_script) -> function (API) -> app + dist bundle.
 *   - every iteration exercises:
 *       * agent message (SSE round-trip: api->DB->redis->worker->mock->SSE)
 *       * file CRUD: create -> update(content) -> download -> delete
 *       * function execute (API/sync, against the fake AgentBox)
 *       * app asset load (serve the uploaded dist bundle)
 *
 * Run (see `make load-test-journey`):
 *   docker run --rm --network host -e LEMMA_API_URL=http://localhost:8000 \
 *     -e MAX_USERS=100 -v ./load_tests:/scripts \
 *     grafana/k6:latest run /scripts/journey.js
 */

import { check, sleep } from "k6";
import http from "k6/http";
import { b64decode } from "k6/encoding";
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

// API (synchronous) function. The fake AgentBox cans schema extraction and echoes
// input on execute, so the run completes without real code execution; the headers
// + models match the real format so create's schema-extraction script is built.
function functionCode(name) {
  return (
    `#input_type_name: LoadInput\n` +
    `#output_type_name: LoadResult\n` +
    `#function_name: ${name}\n\n` +
    `from pydantic import BaseModel\n` +
    `from lemma_sdk import FunctionContext\n\n` +
    `class LoadInput(BaseModel):\n    n: int\n\n` +
    `class LoadResult(BaseModel):\n    doubled: int\n\n` +
    `async def ${name}(ctx: FunctionContext, data: LoadInput) -> LoadResult:\n` +
    `    return LoadResult(doubled=data.n * 2)\n`
  );
}

// Minimal valid dist bundle (a zip whose root has index.html). Decoded to binary
// and uploaded as the app's dist_archive so app asset serving has a release.
const DIST_ZIP_B64 =
  "UEsDBBQAAAAIADR/2lzq0O+YRQAAAGIAAAAKAAAAaW5kZXguaHRtbLNRTMlPLqksSFXIKMnNsbOBkqmJKXY2JZklOal2PvmJKSGpxSUKjgUFNvoQMRt9iIqk/JRKoGpDuCKghCFQFiKuDzYMAFBLAQIUAxQAAAAIADR/2lzq0O+YRQAAAGIAAAAKAAAAAAAAAAAAAACAAQAAAABpbmRleC5odG1sUEsFBgAAAAABAAEAOAAAAG0AAAAAAA==";

// --------------------------------------------------------------------------
// Custom metrics — one Trend per API for avg + p50/p90/p95/p99
// --------------------------------------------------------------------------

const signupMs = new Trend("signup_ms", true);
const createOrgMs = new Trend("create_org_ms", true);
const createPodMs = new Trend("create_pod_ms", true);
const createAgentMs = new Trend("create_agent_ms", true);
const createConvMs = new Trend("create_conv_ms", true);
const createFunctionMs = new Trend("create_function_ms", true);
const createAppMs = new Trend("create_app_ms", true);
const uploadBundleMs = new Trend("upload_bundle_ms", true);
const messageRoundtripMs = new Trend("message_roundtrip_ms", true);
const createFileMs = new Trend("create_file_ms", true);
const updateFileMs = new Trend("update_file_ms", true);
const downloadFileMs = new Trend("download_file_ms", true);
const deleteFileMs = new Trend("delete_file_ms", true);
const executeFunctionMs = new Trend("execute_function_ms", true);
const loadAppAssetMs = new Trend("load_app_asset_ms", true);

const provisionSuccess = new Rate("provision_success");
const messageSuccess = new Rate("message_success");
const fileSuccess = new Rate("file_success");
const functionSuccess = new Rate("function_success");
const appSuccess = new Rate("app_success");
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
    journey_errors: ["count<200"],
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
// Provision: signup -> org -> pod -> agent -> conversation -> function -> app
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

  // Function (API/sync). Best-effort: a failure here doesn't abort the VU; the
  // loop simply skips function execution.
  let functionName = null;
  const fname = `loadfn_${__VU}_${Date.now()}`;
  resp = http.post(
    `${API_URL}/pods/${podId}/functions`,
    JSON.stringify({
      name: fname,
      description: "load-test function",
      type: "API",
      code: functionCode(fname),
    }),
    { headers: H, tags: { name: "create_function" }, timeout: "60s" }
  );
  createFunctionMs.add(resp.timings.duration);
  if (resp.status === 201) {
    functionName = resp.json("name") || fname;
  } else {
    journeyErrors.add(1);
  }

  // App + dist bundle. Best-effort, same as the function.
  let appName = null;
  const aname = `loadapp${__VU}x${Date.now()}`;
  resp = http.post(
    `${API_URL}/pods/${podId}/apps`,
    JSON.stringify({ name: aname }),
    { headers: H, tags: { name: "create_app" }, timeout: "30s" }
  );
  createAppMs.add(resp.timings.duration);
  if (resp.status === 201) {
    appName = resp.json("name") || aname;
    const up = http.post(
      `${API_URL}/pods/${podId}/apps/${appName}/bundle`,
      {
        dist_archive: http.file(
          b64decode(DIST_ZIP_B64, "std", "b"),
          "dist.zip",
          "application/zip"
        ),
      },
      { headers: { Authorization: H.Authorization }, tags: { name: "upload_bundle" }, timeout: "60s" }
    );
    uploadBundleMs.add(up.timings.duration);
    if (up.status !== 200 && up.status !== 201) {
      appName = null;
      journeyErrors.add(1);
    }
  } else {
    journeyErrors.add(1);
  }

  provisionSuccess.add(true);
  return { podId, convId, H, functionName, appName };
}

// --------------------------------------------------------------------------
// VU: provision once, then exercise the full surface in a loop (sustained)
// --------------------------------------------------------------------------

export function journeyVU() {
  if (!vuState) {
    vuState = provision();
    if (!vuState) {
      sleep(1);
      return;
    }
  }
  const podId = vuState.podId;
  const H = vuState.H;
  const authOnly = { Authorization: H.Authorization };

  // 1. Agent message — POST /messages returns text/event-stream and blocks until
  // the run reaches a terminal event; full duration = the worker round-trip.
  const resp = http.post(
    `${API_URL}/pods/${podId}/conversations/${vuState.convId}/messages`,
    JSON.stringify({ content: MESSAGE }),
    { headers: H, tags: { name: "message" }, timeout: "120s", responseType: "text" }
  );
  messageRoundtripMs.add(resp.timings.duration);
  const body = resp.body || "";
  const completed = resp.status === 200 && body.indexOf('"type": "completed"') !== -1;
  messageSuccess.add(completed);
  if (!completed) journeyErrors.add(1);
  const tokens = countOccurrences(body, '"type": "token"');
  if (tokens > 0) tokensReceived.add(tokens);
  if (body.indexOf("write_todos") !== -1) toolCallsReceived.add(1);
  check(resp, { "message run completed": () => completed });

  // 2. File CRUD: create -> update(content) -> download -> delete. Each must
  // resolve/persist in short UoWs and never pin a DB connection across storage.
  const fpath = `/lt-${__VU}-${__ITER}.txt`;
  const fname = `lt-${__VU}-${__ITER}.txt`;
  let fileOk = false;
  const up = http.post(
    `${API_URL}/pods/${podId}/datastore/files`,
    {
      data: http.file("hello from loadtest\n", fname, "text/plain"),
      name: fname,
      directory_path: "/",
      search_enabled: "false",
    },
    { headers: authOnly, tags: { name: "create_file" }, timeout: "30s" }
  );
  createFileMs.add(up.timings.duration);
  if (up.status === 200 || up.status === 201) {
    // Update file content (PATCH multipart).
    const updt = http.patch(
      `${API_URL}/pods/${podId}/datastore/files/by-path`,
      {
        path: fpath,
        data: http.file("updated by loadtest\n", fname, "text/plain"),
      },
      { headers: authOnly, tags: { name: "update_file" }, timeout: "30s" }
    );
    updateFileMs.add(updt.timings.duration);

    const dl = http.get(
      `${API_URL}/pods/${podId}/datastore/files/download?path=${encodeURIComponent(fpath)}`,
      { headers: authOnly, tags: { name: "download_file" }, timeout: "30s" }
    );
    downloadFileMs.add(dl.timings.duration);

    const del = http.del(
      `${API_URL}/pods/${podId}/datastore/files/by-path?path=${encodeURIComponent(fpath)}`,
      null,
      { headers: authOnly, tags: { name: "delete_file" }, timeout: "30s" }
    );
    deleteFileMs.add(del.timings.duration);

    fileOk =
      (updt.status === 200) &&
      (dl.status === 200) &&
      (del.status === 204 || del.status === 200);
  }
  fileSuccess.add(fileOk);
  if (!fileOk) journeyErrors.add(1);

  // 3. Function execute (API/sync) — against the fake AgentBox. The run executes
  // inline and the response carries the terminal status.
  if (vuState.functionName) {
    const fx = http.post(
      `${API_URL}/pods/${podId}/functions/${vuState.functionName}/runs`,
      JSON.stringify({ input_data: { n: __ITER } }),
      { headers: H, tags: { name: "execute_function" }, timeout: "60s" }
    );
    executeFunctionMs.add(fx.timings.duration);
    const fnOk = fx.status === 200 && (fx.json("status") || "") === "COMPLETED";
    functionSuccess.add(fnOk);
    if (!fnOk) journeyErrors.add(1);
  }

  // 4. App asset load — serve the uploaded dist bundle's root asset.
  if (vuState.appName) {
    const asset = http.get(
      `${API_URL}/pods/${podId}/apps/${vuState.appName}/assets`,
      { headers: authOnly, tags: { name: "load_app_asset" }, timeout: "30s" }
    );
    loadAppAssetMs.add(asset.timings.duration);
    const appOk = asset.status === 200;
    appSuccess.add(appOk);
    if (!appOk) journeyErrors.add(1);
  }

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
  ["create_function", "create_function_ms"],
  ["create_app", "create_app_ms"],
  ["upload_bundle", "upload_bundle_ms"],
  ["message_roundtrip", "message_roundtrip_ms"],
  ["create_file", "create_file_ms"],
  ["update_file", "update_file_ms"],
  ["download_file", "download_file_ms"],
  ["delete_file", "delete_file_ms"],
  ["execute_function", "execute_function_ms"],
  ["load_app_asset", "load_app_asset_ms"],
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
  const rate = (key) => fmt((((m[key] && m[key].values) || {}).rate || 0) * 100) + "%";
  const cnt = (key) => fmt((m[key] && m[key].values && m[key].values.count));
  lines.push("provision_success rate : " + rate("provision_success"));
  lines.push("message_success rate   : " + rate("message_success"));
  lines.push("file_success rate      : " + rate("file_success"));
  lines.push("function_success rate  : " + rate("function_success"));
  lines.push("app_success rate       : " + rate("app_success"));
  lines.push("tokens_received        : " + cnt("tokens_received"));
  lines.push("tool_calls_received    : " + cnt("tool_calls_received"));
  lines.push("journey_errors         : " + cnt("journey_errors"));
  lines.push("======================================================");
  lines.push("");

  const out = {};
  out["stdout"] = lines.join("\n");
  out[SUMMARY_PATH] = JSON.stringify(data, null, 2);
  return out;
}
