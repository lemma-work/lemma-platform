/**
 * Decomposition micro-benchmark to localize the API bottleneck.
 *
 *   MODE=health : GET /health        (auth-excluded; pure ASGI + middleware stack, no DB, no auth)
 *   MODE=auth   : GET /organizations (verify_auth/get_session + one session-per-request DB read)
 *   MODE=pods   : GET /pods/organization/{id}      (auth + org-member read + the pod list query)
 *   MODE=home   : GET /organizations/{id}/home     (auth + the whole landing-page tree)
 *
 * The modes are cumulative on purpose, so subtracting them attributes the cost:
 * `auth - health` is what authentication costs, `pods - auth` is what the pod
 * handler itself costs, and `home - auth` is what the landing page costs. A
 * number that only shows up in one mode belongs to that mode's code.
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

const AUTHED_MODES = ["auth", "pods", "home"];
// Enough pods that a per-pod query would show up as a slope rather than hide in
// the noise of a single-pod org.
const SEED_PODS = parseInt(__ENV.SEED_PODS || "10", 10);

export function setup() {
  if (!AUTHED_MODES.includes(MODE)) return {};
  const email = `micro+${Date.now()}@example.com`;
  const r = http.post(
    `${API}/st/auth/signup`,
    JSON.stringify({ formFields: [{ id: "email", value: email }, { id: "password", value: PASSWORD }] }),
    { headers: { "Content-Type": "application/json" } }
  );
  const tok =
    r.headers["St-Access-Token"] || r.headers["st-access-token"] || r.headers["ST-ACCESS-TOKEN"] || "";
  if (!tok) throw new Error(`signup failed: ${r.status}`);
  if (MODE === "auth") return { token: tok };

  const auth = { headers: { Authorization: `Bearer ${tok}`, "Content-Type": "application/json" } };
  const org = http.post(`${API}/organizations`, JSON.stringify({ name: `Micro Org ${Date.now()}` }), auth);
  if (org.status >= 300) throw new Error(`org create failed: ${org.status} ${org.body}`);
  const orgId = org.json("id");

  for (let i = 0; i < SEED_PODS; i++) {
    const suffix = `${Date.now()}-${i}`;
    const pod = http.post(
      `${API}/pods`,
      JSON.stringify({ name: `Micro Pod ${suffix}`, type: "ASSISTANT", organization_id: orgId }),
      auth
    );
    if (pod.status >= 300) throw new Error(`pod create failed: ${pod.status} ${pod.body}`);
  }
  return { token: tok, orgId };
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
  } else if (MODE === "pods") {
    r = http.get(`${API}/pods/organization/${data.orgId}`, {
      headers: { Authorization: `Bearer ${data.token}` },
      tags: { name: "pod_list" },
    });
  } else if (MODE === "home") {
    r = http.get(`${API}/organizations/${data.orgId}/home`, {
      headers: { Authorization: `Bearer ${data.token}` },
      tags: { name: "org_home" },
    });
  } else {
    r = http.get(`${API}/organizations`, {
      headers: { Authorization: `Bearer ${data.token}` },
      tags: { name: "auth_get" },
    });
  }
  check(r, { "2xx": (x) => x.status >= 200 && x.status < 300 });
}
