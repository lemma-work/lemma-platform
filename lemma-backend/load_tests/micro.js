/**
 * Decomposition micro-benchmark to localize the API bottleneck.
 *
 *   MODE=health : GET /health        (auth-excluded; pure ASGI + middleware stack, no DB, no auth)
 *   MODE=auth   : GET /organizations (verify_auth/get_session + one session-per-request DB read)
 *
 * Run one mode at a time at a fixed concurrency so container CPU can be attributed.
 *   docker run --rm --network host -e LEMMA_API_URL=http://localhost:8000 \
 *     -e MODE=health -e VUS=100 -e DUR=15s -v ./load_tests:/scripts grafana/k6 run /scripts/micro.js
 */
import http from "k6/http";
import { check } from "k6";

const API = __ENV.LEMMA_API_URL || "http://localhost:8000";
const MODE = __ENV.MODE || "health";
const VUS = parseInt(__ENV.VUS || "100", 10);
const DUR = __ENV.DUR || "15s";
const PASSWORD = "LoadTest@12345";

export const options = {
  scenarios: { m: { executor: "constant-vus", vus: VUS, duration: DUR } },
  summaryTrendStats: ["avg", "min", "med", "p(90)", "p(95)", "p(99)", "max"],
};

export function setup() {
  if (MODE !== "auth") return {};
  const email = `micro+${Date.now()}@example.com`;
  const r = http.post(
    `${API}/st/auth/signup`,
    JSON.stringify({ formFields: [{ id: "email", value: email }, { id: "password", value: PASSWORD }] }),
    { headers: { "Content-Type": "application/json" } }
  );
  const tok =
    r.headers["St-Access-Token"] || r.headers["st-access-token"] || r.headers["ST-ACCESS-TOKEN"] || "";
  if (!tok) throw new Error(`signup failed: ${r.status}`);
  return { token: tok };
}

export default function (data) {
  let r;
  if (MODE === "health") {
    r = http.get(`${API}/health`, { tags: { name: "health" } });
  } else if (MODE === "signup") {
    const email = `sg+${__VU}-${__ITER}-${Date.now()}@example.com`;
    r = http.post(
      `${API}/st/auth/signup`,
      JSON.stringify({ formFields: [{ id: "email", value: email }, { id: "password", value: PASSWORD }] }),
      { headers: { "Content-Type": "application/json" }, tags: { name: "signup" } }
    );
  } else {
    r = http.get(`${API}/organizations`, {
      headers: { Authorization: `Bearer ${data.token}` },
      tags: { name: "auth_get" },
    });
  }
  check(r, { "2xx": (x) => x.status >= 200 && x.status < 300 });
}
