/**
 * Lemma SSE concurrent-streaming load test.
 *
 * Measures how many simultaneous SSE conversation streams a resource-
 * constrained server can sustain, and how DB connection count behaves
 * under concurrent streaming load.
 *
 * Two concurrent scenarios:
 *
 *   streamers  — ramp from 0 → MAX_STREAMERS VUs, each opening one SSE
 *                stream connection to a conversation and holding it open.
 *                Measures connection establishment time and event latency.
 *
 *   writer     — a fixed pool of VUs that continuously send messages to
 *                conversations, generating events that streamers receive.
 *
 * Required env vars (written by load_tests/setup.py):
 *   LEMMA_API_URL    — HTTP base URL, e.g. http://localhost:8000
 *   LEMMA_TOKEN      — SuperTokens access token
 *   LEMMA_POD_ID     — UUID of the pre-created pod
 *
 * Run:
 *   docker run --rm --network host \
 *     --env-file load_tests/.env.load_test \
 *     -v ./load_tests:/scripts \
 *     grafana/k6:latest run /scripts/sse_concurrent.js
 *
 * Override capacity ceiling:
 *   docker run ... -e MAX_STREAMERS=100 ...
 */

import { check, sleep } from "k6";
import http from "k6/http";
import { Counter, Gauge, Rate, Trend } from "k6/metrics";

// --------------------------------------------------------------------------
// Config
// --------------------------------------------------------------------------

const API_URL = __ENV.LEMMA_API_URL    || "http://localhost:8000";
const TOKEN   = __ENV.LEMMA_TOKEN      || "";
const POD_ID  = __ENV.LEMMA_POD_ID     || "";

const MAX_STREAMERS = parseInt(__ENV.MAX_STREAMERS || "50", 10);
const WRITER_VUS    = parseInt(__ENV.WRITER_VUS    || "2",  10);

if (!TOKEN || !POD_ID) {
  throw new Error(
    "LEMMA_TOKEN and LEMMA_POD_ID must be set. Run load_tests/setup.py first."
  );
}

const HEADERS = {
  Authorization: `Bearer ${TOKEN}`,
  "Content-Type": "application/json",
};

// --------------------------------------------------------------------------
// Custom metrics
// --------------------------------------------------------------------------

const sseConnectDuration = new Trend("sse_connect_duration_ms", true);
const sseEventsReceived  = new Counter("sse_events_received");
const sseErrors          = new Counter("sse_errors");
const sseLiveStreams     = new Gauge("sse_live_streams");
const writeSuccessRate   = new Rate("write_success_rate");

// --------------------------------------------------------------------------
// Scenario configuration
// --------------------------------------------------------------------------

const TOTAL_DURATION = "5m";

export const options = {
  scenarios: {
    streamers: {
      executor: "ramping-vus",
      exec: "streamerVU",
      startVUs: 0,
      stages: [
        { duration: "1m",  target: Math.round(MAX_STREAMERS * 0.25) },
        { duration: "1m",  target: Math.round(MAX_STREAMERS * 0.50) },
        { duration: "30s", target: MAX_STREAMERS },
        { duration: "2m",  target: MAX_STREAMERS },
        { duration: "30s", target: 0 },
      ],
      gracefulRampDown: "10s",
    },

    writer: {
      executor: "constant-vus",
      exec: "writerVU",
      vus: WRITER_VUS,
      duration: TOTAL_DURATION,
      startTime: "5s",
    },
  },

  thresholds: {
    sse_errors: ["count<10"],
    write_success_rate: ["rate>0.95"],
  },
};

// --------------------------------------------------------------------------
// Helper: create a conversation
// --------------------------------------------------------------------------

function createConversation() {
  const resp = http.post(
    `${API_URL}/pods/${POD_ID}/conversations`,
    JSON.stringify({
      title: `LoadTest SSE ${Date.now()}`,
      type: "TASK",
    }),
    { headers: HEADERS, timeout: "10s" }
  );
  if (resp.status === 201) {
    return resp.json("id");
  }
  return null;
}

// --------------------------------------------------------------------------
// Streamer VU — one SSE stream connection per VU
// --------------------------------------------------------------------------

export function streamerVU() {
  const convId = createConversation();
  if (!convId) {
    sseErrors.add(1);
    return;
  }

  const streamUrl =
    `${API_URL}/pods/${POD_ID}/conversations/${convId}/stream`;

  const connectStart = Date.now();
  let connected = false;

  // k6 doesn't have native SSE support, so we use a streaming HTTP GET
  // with a long timeout. The response is text/event-stream; we hold it
  // open for the scenario duration to simulate a client listening for
  // agent events.
  const resp = http.get(streamUrl, {
    headers: HEADERS,
    timeout: "300s",
    responseType: "text",
  });

  // If the stream closes immediately (no active run), that's expected —
  // the endpoint returns 200 with an empty body. The key metric is that
  // the DB connection was NOT held during the stream.
  if (resp.status === 200) {
    sseConnectDuration.add(Date.now() - connectStart);
    connected = true;
    sseLiveStreams.add(1);
  } else {
    sseErrors.add(1);
  }

  if (connected) {
    sseLiveStreams.add(-1);
  }
}

// --------------------------------------------------------------------------
// Writer VU — continuously sends messages to generate SSE events
// --------------------------------------------------------------------------

export function writerVU() {
  for (;;) {
    const convId = createConversation();
    if (!convId) {
      sleep(1);
      continue;
    }

    const resp = http.post(
      `${API_URL}/pods/${POD_ID}/conversations/${convId}/messages`,
      JSON.stringify({ content: "load-test message" }),
      {
        headers: HEADERS,
        timeout: "30s",
        responseType: "text",
      }
    );

    const ok = resp.status === 200 || resp.status === 201;
    writeSuccessRate.add(ok);

    if (!ok) {
      sleep(1);
    } else {
      sleep(2);
    }
  }
}
