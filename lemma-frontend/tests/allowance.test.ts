import test from "node:test";
import assert from "node:assert/strict";
import type { MyUsageLimitsResponse, UsageAllowanceResponse } from "lemma-sdk";
import {
    allowanceOf, bindingWindow, breakdown, costState, formatCost, formatPercent,
    planLabel, resetsIn, worthShowing,
} from "../src/usage/allowance.ts";

function window(over: Partial<UsageAllowanceResponse> = {}): UsageAllowanceResponse {
    return { key: "user_weekly", label: "Your weekly allowance", used_percent: 10, allowed: true, reset_at: "2026-09-25T00:00:00Z", ...over };
}
function limits(over: Partial<MyUsageLimitsResponse> = {}): MyUsageLimitsResponse {
    return { organization_id: null, plan_name: null, plan_type: null, warning_percent: 80, allowed: true, windows: [], ...over };
}

test("a deployment that caps nothing is not a person who has spent nothing", () => {
    // `windows: []` is what an uncapped deployment returns, and it is the
    // default: limits are opt-in. Reading that as 0% puts an empty bar on
    // screen for a limit that does not exist.
    assert.equal(allowanceOf(limits()).kind, "uncapped");
    assert.equal(allowanceOf(undefined).kind, "uncapped");
    assert.equal(bindingWindow(limits()), null);
    assert.equal(worthShowing(limits()), false);
});

test("the window that matters is the one nearest to stopping you", () => {
    // Three windows apply at once and any one refuses on its own, so an
    // average would describe a situation nobody is in.
    const state = allowanceOf(limits({ windows: [
        window({ key: "user_weekly", used_percent: 10 }),
        window({ key: "org_monthly", used_percent: 99 }),
        window({ key: "user_monthly", used_percent: 12 }),
    ] }));

    assert.equal(state.kind, "within");
    assert.equal(state.kind === "within" && state.window.key, "org_monthly");
});

test("a window that has already refused outranks a busier one that has not", () => {
    // Being refused is the reason the next run fails, whatever the percentages
    // read — a blocked window at 100% must win over an allowed one at 99.9%.
    const state = allowanceOf(limits({ windows: [
        window({ key: "user_monthly", used_percent: 99.9, allowed: true }),
        window({ key: "user_weekly", used_percent: 100, allowed: false }),
    ] }));

    assert.equal(state.kind, "blocked");
    assert.equal(state.kind === "blocked" && state.window.key, "user_weekly");
    assert.equal(worthShowing(limits({ windows: [window({ used_percent: 100, allowed: false })] })), true);
});

test("the warning threshold is the server's, not ours", () => {
    // A deployment warning at half its cap says so in usage_limit_warn_fraction.
    // A hardcoded 80 here would silently disagree with the backend that emits
    // the warning, and the two would tell people different things.
    const half = limits({ warning_percent: 50, windows: [window({ used_percent: 60 })] });
    const eighty = limits({ warning_percent: 80, windows: [window({ used_percent: 60 })] });

    assert.equal(allowanceOf(half).kind === "within" && allowanceOf(half).warn, true);
    assert.equal(allowanceOf(eighty).kind === "within" && allowanceOf(eighty).warn, false);
    assert.equal(worthShowing(half), true);
    assert.equal(worthShowing(eighty), false);
});

test("a nonsensical threshold falls back rather than warning at everything", () => {
    for (const bad of [0, -5, 140, Number.NaN]) {
        const state = allowanceOf(limits({ warning_percent: bad, windows: [window({ used_percent: 12 })] }));
        assert.equal(state.kind === "within" && state.warn, false, "warned at 12% with threshold " + bad);
    }
});

test("an allowance says nothing until it is worth saying", () => {
    // A bar sitting at 4% all month is chrome people learn to stop looking at.
    assert.equal(worthShowing(limits({ windows: [window({ used_percent: 4 })] })), false);
    assert.equal(worthShowing(limits({ windows: [window({ used_percent: 81 })] })), true);
});

test("small money reads as small, not as free", () => {
    // A short run costs a fraction of a cent. Rounding it to $0.00 reports
    // spending that happened as spending that did not.
    assert.equal(formatCost(0.00002), "<$0.0001");
    assert.equal(formatCost(0.00002, true), "$0.00002");
    assert.equal(formatCost(0), "$0.00");
    assert.equal(formatCost(0.5), "$0.50");        // USD keeps two digits as a floor
    assert.equal(formatCost(0.12345), "$0.1235");  // and gains them only where they carry information
    assert.equal(formatCost(12.5), "$12.50");
    assert.equal(formatCost(null), "Unavailable");
    assert.equal(formatCost(Number.NaN), "Unavailable");
});

test("a percentage never rounds into a lie", () => {
    // 99.7% of a cap must not read as 100% while the run is still allowed.
    assert.equal(formatPercent(99.7, true), ">99%");
    assert.equal(formatPercent(100, false), "100%");
    assert.equal(formatPercent(0.4, true), "<1%");
    assert.equal(formatPercent(0, true), "0%");
    assert.equal(formatPercent(64.9, true), "64%");
});

test("a cost that has not been priced yet does not report as zero", () => {
    // Metering runs ahead of pricing, so a finished run can hold a null cost
    // for a while — and an unpriced model never gets one at all.
    assert.equal(costState({ cost_usd: null, metadata: { metering_state: "PENDING" } }), "Pending");
    assert.equal(costState({ cost_usd: null, metadata: { metering_state: "UNCONFIRMED" } }), "Awaiting usage");
    assert.equal(costState({ cost_usd: 0.2, metadata: { metering_state: "UNPRICED" } }), "Cost unavailable");
    assert.equal(costState({ cost_usd: null, metadata: {} }), "Cost unavailable");
    assert.equal(costState({ cost_usd: 0.01, metadata: {} }), "Recorded");
});

test("a breakdown keeps an unpriced line rather than sorting it away as free", () => {
    const rows = breakdown({
        "claude-opus": { system_cost_usd: 1.25, total_tokens: 900 },
        "local-llama": { total_tokens: 50_000 },
        "claude-haiku": { system_cost_usd: 0.02, total_tokens: 400 },
    });

    assert.deepEqual(rows.map(r => r.label), ["claude-opus", "claude-haiku", "local-llama"]);
    assert.equal(rows[2].cost, null, "an unpriced model has no cost, which is not a cost of zero");
    assert.equal(rows[2].tokens, 50_000);
    assert.deepEqual(breakdown(undefined), []);
});

test("the plan says whose money it is", () => {
    assert.equal(planLabel(limits({ plan_type: "TEAM", plan_name: "Scale" })), "Organization plan · Scale");
    assert.equal(planLabel(limits({ plan_type: "PERSONAL" })), "Your plan");
    assert.equal(planLabel(limits()), "Usage limits");
});

test("a reset reads in the units a person would use", () => {
    const now = new Date("2026-09-18T12:00:00Z");

    assert.equal(resetsIn("2026-09-18T12:30:00Z", now), "resets in under an hour");
    assert.equal(resetsIn("2026-09-18T13:00:00Z", now), "resets in 1 hour");
    assert.equal(resetsIn("2026-09-19T12:00:00Z", now), "resets in 1 day");
    assert.equal(resetsIn("2026-09-25T12:00:00Z", now), "resets in 7 days");
    assert.equal(resetsIn("2026-09-18T11:00:00Z", now), "resets now");
    assert.equal(resetsIn(null, now), "");
    assert.equal(resetsIn("not a date", now), "");
});
