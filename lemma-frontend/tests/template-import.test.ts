import test from "node:test";
import assert from "node:assert/strict";
import { GIVE_UP_MS, TemplateImportError, importTemplate, type Call, type TemplateImportJob } from "../src/stage/template-import.ts";

function job(status: TemplateImportJob["status"], extra: Partial<TemplateImportJob> = {}): TemplateImportJob {
    return { import_id: "imp-1", pod_id: "pod-1", status, plan: null, error: null, ...extra };
}

/** A server that answers from a script, and remembers what it was asked. */
function scripted(answers: TemplateImportJob[]) {
    const asked: { method: string; path: string; body?: Record<string, unknown> }[] = [];
    const call: Call = async <T,>(method: "GET" | "POST", path: string, body?: Record<string, unknown>) => {
        asked.push({ method, path, body });
        const next = answers.shift();
        if (!next) throw new Error("asked more than the script holds: " + method + " " + path);
        return next as T;
    };
    return { call, asked };
}

test("a template is started, applied with its defaults, and waited on until it completes", async () => {
    const server = scripted([
        job("QUEUED"),
        job("PLANNING"),
        job("AWAITING_CONFIRMATION", { plan: { variables: [{ name: "timezone", default: "UTC" }, { name: "note", default: null }] } }),
        job("APPLYING"),
        job("COMPLETED"),
    ]);
    const seen: string[] = [];
    const done = await importTemplate({ podId: "pod-1", template: "support-desk", call: server.call, sleep: async () => {}, onStatus: (status) => seen.push(status) });

    assert.equal(done.status, "COMPLETED");
    assert.deepEqual(server.asked[0], { method: "POST", path: "/pods/pod-1/bundle/imports", body: { kind: "TEMPLATE", template: "support-desk" } });
    const apply = server.asked.find((one) => one.path.endsWith("/apply"));
    // Nobody is asked: every variable takes its default, and one without a
    // default is sent empty rather than left out.
    assert.deepEqual(apply?.body, { variables: { timezone: "UTC", note: "" }, confirm_destructive: false });
    assert.deepEqual(seen, ["QUEUED", "PLANNING", "AWAITING_CONFIRMATION", "APPLYING", "COMPLETED"]);
});

test("a failed plan stops the hire with the server's reason", async () => {
    const server = scripted([job("QUEUED"), job("FAILED", { error: "Table 'scorecard' already exists with other columns." })]);
    await assert.rejects(
        importTemplate({ podId: "pod-1", template: "support-desk", call: server.call, sleep: async () => {} }),
        (error: unknown) => error instanceof TemplateImportError && /already exists/.test(error.message),
    );
    // Nothing was applied after the failure.
    assert.equal(server.asked.some((one) => one.path.endsWith("/apply")), false);
});

test("a job that never settles is given up on rather than polled forever", async () => {
    let clock = 0;
    const answers = [job("QUEUED"), ...Array.from({ length: 500 }, () => job("PLANNING"))];
    const server = scripted(answers);
    await assert.rejects(
        importTemplate({
            podId: "pod-1",
            template: "follow-ups",
            call: server.call,
            sleep: async (ms) => { clock += ms; },
            now: () => clock,
        }),
        (error: unknown) => error instanceof TemplateImportError && /taking too long/.test(error.message),
    );
    assert.ok(clock > GIVE_UP_MS);
});
