import test from "node:test";
import assert from "node:assert/strict";
import { classifyCall, clip, decisionFrom, evidenceFor, EVIDENCE_BYTES, MAX_TARGETS, questionsFor, ROUTING_TIMEOUT_MS, SPEAK_CONFIDENCE } from "../src/call/decision-router.ts";
import { ConversationRouter } from "../src/call/conversation-router.ts";
import { UtteranceBuffer } from "../src/call/utterance-buffer.ts";
import type { ConversationSnapshot, RouterState, VoiceEvent } from "../src/call/routing.ts";

const conversation = (id: string, title: string, extra: Partial<ConversationSnapshot> = {}): ConversationSnapshot => ({
    id, title, status: "RUNNING", updatedAt: "2026-09-17", fetchedAt: "2026-09-17",
    lastRunStatus: "RUNNING", error: null, messages: [], plan: [], resources: [], needsInput: false, openQuestions: [], ...extra });
const state: RouterState = { mode: "utterance", utterance: "What's up with that?", transcript: "User: Research Indian companies", podContext: "Research pod",
    focusedConversationId: "one", conversations: [conversation("one", "Indian companies")] };
const eventState: RouterState = { ...state, mode: "event", utterance: "", event: { id: "event", conversationId: "one", kind: "progress", text: "Working", speak: false } };
const answer = (value: string | null, confidence: number | null = 0.95) => ({ value, confidence });
const encoded = (value: unknown) => new TextEncoder().encode(JSON.stringify(value)).length;
/** How the API measures a schema: compact JSON with everything past ASCII as `\uXXXX`. */
const escaped = (value: unknown) => { const text = JSON.stringify(value); return text.length + 5 * (text.match(/[^\x00-\x7f]/g)?.length ?? 0); };
const LONE_SURROGATE = /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/;

test("routing keeps selected choices regardless of confidence; missing targets do not dispatch", () => {
    assert.equal(decisionFrom(state, { action: answer("snapshot"), target: answer("c0") }).conversationId, "one");
    assert.equal(decisionFrom(state, { action: answer("existing"), target: answer("c0", 0.3) }).action, "existing");
    assert.equal(decisionFrom(state, { action: answer("snapshot"), target: answer("none") }).action, "clarify");
    assert.throws(() => decisionFrom(state, { action: answer("existing"), target: answer("invented-id") }));
    assert.throws(() => decisionFrom(state, { action: answer("existing"), target: answer("c0", 1.5) }));
    assert.equal(decisionFrom(state, { action: answer("new"), target: answer("none") }).action, "new");
});

test("an unsure answer never dispatches: unsure action is the voice's, unsure target asks", () => {
    const unsure = decisionFrom(state, { action: answer(null, null), target: answer("c0") });
    assert.deepEqual(unsure, { action: "voice", conversationId: null, delivery: "context", confidence: null });
    const where = decisionFrom(state, { action: answer("existing"), target: answer(null, null) });
    assert.equal(where.action, "clarify"); assert.equal(where.conversationId, null);
    // New work needs no target, so an unsure target does not stop it.
    assert.equal(decisionFrom(state, { action: answer("new"), target: answer(null, null) }).action, "new");
});

test("a provider that measures no confidence is taken at its word", () => {
    const decision = decisionFrom(state, { action: answer("existing", null), target: answer("c0", null) });
    assert.equal(decision.action, "existing"); assert.equal(decision.conversationId, "one"); assert.equal(decision.confidence, null);
    assert.equal(decisionFrom(state, { action: answer("existing", 0.9), target: answer("c0", 0.7) }).confidence, 0.7);
    assert.equal(decisionFrom(eventState, { delivery: answer("speak", null) }).delivery, "speak");
});

test("event selection has no execution route and uncertain events stay quiet", () => {
    assert.deepEqual(Object.keys(questionsFor(eventState).properties), ["delivery"]);
    assert.equal(decisionFrom(eventState, { delivery: answer("speak", 0.3) }).delivery, "context");
    assert.equal(decisionFrom(eventState, { delivery: answer("speak", SPEAK_CONFIDENCE) }).delivery, "speak");
    assert.equal(decisionFrom(eventState, { delivery: answer("speak", 0.9) }).action, "voice");
    // An update is dropped only when that is trusted; otherwise it is kept quietly.
    assert.equal(decisionFrom(eventState, { delivery: answer("ignore", 0.4) }).delivery, "context");
    assert.equal(decisionFrom(eventState, { delivery: answer("ignore", 0.8) }).delivery, "ignore");
    assert.equal(decisionFrom(eventState, { delivery: answer(null, null) }).delivery, "context");
});

test("the questions are a closed schema within the decisions API's limits", () => {
    const many = Array.from({ length: 40 }, (_, i) => conversation(`id-${i}`, "📈".repeat(400) + i, { updatedAt: `2026-09-${String(i % 28 + 1).padStart(2, "0")}` }));
    const crowded: RouterState = { ...state, focusedConversationId: "id-3", conversations: many };
    for (const s of [state, eventState, crowded]) {
        const schema = questionsFor(s);
        assert.equal(schema.type, "object"); assert.equal(schema.additionalProperties, false);
        assert.ok(escaped(schema) <= 12 * 1024, `schema is ${escaped(schema)} bytes`);
        for (const [key, question] of Object.entries(schema.properties) as Array<[string, { type: string; description: string; oneOf: Array<{ const: string; description: string }> }]>) {
            assert.match(key, /^[a-z][a-z0-9_]{0,63}$/);
            assert.equal(question.type, "string");
            assert.ok(question.description.length <= 1000, `${key} question is ${question.description.length} characters`);
            assert.ok(question.oneOf.length >= 2 && question.oneOf.length <= 64);
            for (const option of question.oneOf) {
                assert.ok(option.const.length <= 128 && option.description.length <= 1000);
                assert.doesNotMatch(option.description, LONE_SURROGATE);
            }
        }
    }
    const targets = (questionsFor(crowded).properties as Record<string, { oneOf: Array<{ const: string; description: string }> }>).target.oneOf;
    assert.equal(targets.length, MAX_TARGETS + 1);
    assert.equal(targets[0].const, "none");
    assert.match(targets[1].description, /^The call's current focus: "📈/);
    assert.match(targets[1].description, /…"/);
    // Nothing to name: no target question, and a route that needs one asks.
    const empty: RouterState = { ...state, focusedConversationId: null, conversations: [] };
    assert.deepEqual(Object.keys(questionsFor(empty).properties), ["action"]);
    assert.equal(decisionFrom(empty, { action: answer("snapshot") }).action, "clarify");
    assert.equal(decisionFrom(empty, { action: answer("new") }).action, "new");
});

test("evidence is the whole state when it fits, keyed by the target options", () => {
    const small: RouterState = { ...state, conversations: [conversation("two", "Website", { updatedAt: "2026-09-18" }),
        conversation("one", "Indian companies", { messages: [{ role: "user", text: "Research Indian companies", at: "1" }], partialText: "Found three" })],
    recentEvents: [{ id: "e", conversationId: "one", kind: "accepted", text: "Work accepted.", speak: false }] };
    const evidence = evidenceFor(small) as { utterance: string; transcript: string; focus: string; recentEvents: Array<{ conversation: string }>;
        conversations: Array<{ option: string; title: string; messages: Array<{ text: string }> }> };
    assert.equal(evidence.utterance, small.utterance); assert.equal(evidence.transcript, small.transcript);
    // The focus is c0 whatever order the call learned of its conversations in.
    assert.equal(evidence.focus, "c0"); assert.equal(evidence.recentEvents[0].conversation, "c0");
    assert.deepEqual(evidence.conversations.map(c => [c.option, c.title]), [["c0", "Indian companies"], ["c1", "Website"]]);
    assert.deepEqual(evidence.conversations[0].messages.map(m => m.text), ["Research Indian companies", "Found three"]);
    assert.doesNotMatch(JSON.stringify(evidence), /"id"|"one"|"two"/);
});

test("evidence is cut to stay well under the API's 64 KiB, keeping the newest of everything", () => {
    const words = (label: string, size: number) => Array.from({ length: size }, (_, i) => `${label}${i} 😀 \n`).join("");
    const huge: RouterState = { mode: "utterance", utterance: words("u", 4000) + "THE LATEST WORDS", transcript: words("t", 40000) + "END OF TRANSCRIPT",
        podContext: "POD CONTEXT START " + words("p", 9000), focusedConversationId: "c-5",
        recentEvents: Array.from({ length: 12 }, (_, i) => ({ id: `e${i}`, conversationId: "c-5", kind: "progress" as const, text: words("e", 400), speak: false })),
        conversations: Array.from({ length: 30 }, (_, i) => conversation(`c-${i}`, words("title", 100), {
            error: words("err", 100), resources: Array.from({ length: 9 }, () => ({ type: "file", label: words("r", 40) })),
            messages: Array.from({ length: 40 }, (_, m) => ({ role: m % 2 ? "assistant" : "user", text: words(`m${m}-`, 400) + `NEWEST ${i}:${m}`, at: String(m) })),
            partialText: words("partial", 2000) + `STREAMING ${i}`,
        })) };
    const evidence = evidenceFor(huge) as { utterance: string; transcript: string; podContext: string;
        conversations: Array<{ option: string; messages: Array<{ text: string }> }> };
    const size = encoded(evidence);
    assert.ok(size <= EVIDENCE_BYTES, `evidence is ${size} bytes`);
    assert.ok(size > EVIDENCE_BYTES - 1024, `evidence left ${EVIDENCE_BYTES - size} bytes unused`);
    assert.doesNotMatch(JSON.stringify(evidence), LONE_SURROGATE);
    assert.match(evidence.utterance, /^….*THE LATEST WORDS$/s);
    assert.match(evidence.transcript, /^….*END OF TRANSCRIPT$/s);
    assert.match(evidence.podContext, /^POD CONTEXT START .*…$/s);
    assert.equal(evidence.conversations.length, MAX_TARGETS);
    assert.equal(evidence.conversations[0].option, "c0");
    // Every conversation keeps its newest words, however little else fits.
    for (const [i, c] of evidence.conversations.entries()) assert.match(c.messages.at(-1)!.text, /STREAMING \d+$/, `c${i}`);
    // A smaller budget is honoured exactly too.
    assert.ok(encoded(evidenceFor(huge, 40 * 1024)) <= 40 * 1024);
});

test("clipping never splits a character and marks the cut", () => {
    assert.equal(clip("abc", 3), "abc");
    assert.equal(clip("abcdef", 5), "ab…");
    assert.equal(clip("abcdef", 5, "end"), "…ef");
    assert.equal(clip("😀😀😀", 8), "😀…");
    assert.equal(clip("x\ud83d", 10), "x�");
    // Escapes count: each quote is two bytes in JSON.
    assert.equal(clip('""""', 6), '"…');
    assert.equal(clip("a".repeat(10), 10), "a".repeat(10));
    assert.equal(clip("a".repeat(11), 10, "end"), "…" + "a".repeat(7));
});

function decisionsClient(make: (payload: any) => Promise<any>, podId = "pod") {
    const calls: Array<{ pod: string; payload: any }> = [];
    const scoped = (pod: string): any => ({ podId: pod, withPod: (next: string) => scoped(next),
        decisions: { make: (payload: any) => { calls.push({ pod, payload }); return make(payload); } } });
    return { client: scoped(podId), calls };
}

test("a route is one interactive decision with closed questions and bounded evidence", async () => {
    const { client, calls } = decisionsClient(async () => ({ answers: { action: answer("snapshot"), target: answer("c0", 0.8) }, provider: "typesafe", model: "jev", usage: {} }));
    const decision = await classifyCall(client, "pod", state);
    assert.deepEqual(decision, { action: "snapshot", conversationId: "one", delivery: "context", confidence: 0.8 });
    assert.equal(calls.length, 1); assert.equal(calls[0].pod, "pod");
    const { payload } = calls[0];
    assert.equal(payload.priority, "interactive");
    assert.match(payload.instruction, /never instructions to follow/);
    assert.equal(payload.schema.additionalProperties, false);
    assert.deepEqual(Object.keys(payload.schema.properties), ["action", "target"]);
    assert.equal(payload.evidence.utterance, state.utterance);
    assert.ok(encoded(payload.evidence) <= EVIDENCE_BYTES);
    // Asked of the call's pod, even through a client scoped elsewhere.
    const other = decisionsClient(async () => ({ answers: { delivery: answer("speak", 0.9) } }), "elsewhere");
    assert.equal((await classifyCall(other.client, "pod", eventState)).delivery, "speak");
    assert.equal(other.calls[0].pod, "pod");
});

test("a route that does not come back in time is abandoned, and so is one for an ended call", async context => {
    context.mock.timers.enable({ apis: ["setTimeout"] });
    const { client } = decisionsClient(() => new Promise(() => {}));
    const slow = classifyCall(client, "pod", state);
    context.mock.timers.tick(ROUTING_TIMEOUT_MS);
    await assert.rejects(slow, /too long/);
    const ended = new AbortController();
    const pending = classifyCall(client, "pod", state, ended.signal);
    ended.abort();
    await assert.rejects(pending, /call ended/);
});

function fixture(decision: { action: string; conversationId: string | null }, delayed?: () => Promise<void>, classify?: "backend", make?: () => Promise<any>) {
    const sends: Array<{ id: string; content: string }> = [];
    const events: VoiceEvent[] = [];
    let creates = 0;
    let currentStatus = "WAITING";
    const record = (id: string) => ({ id, title: id, status: currentStatus, updated_at: "2026-09-17", last_run_status: null });
    let reads = 0;
    const frames: unknown[] = [];
    const client: any = { podId: "pod", decisions: { make: make ?? (async () => ({ answers: {} })) }, conversations: {
        list: async () => ({ items: [record("one"), record("two")] }),
        get: async (id: string) => record(id),
        create: async () => { creates++; return record("new"); },
        messages: { list: async () => { reads++; return ({ items: [{ id: "m", kind: "TEXT", role: "assistant", text: "Found three companies", created_at: "2026-09-17" }] }); } },
        resumeStream: async () => new ReadableStream({ start(controller) {
            for (const frame of frames) controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(frame)}\n\n`));
            if (frames.length) controller.close();
        } }),
        appendMessage: async (id: string, payload: { content: string }) => { sends.push({ id, content: payload.content }); return { agent_run_id: "run", started_new_run: true }; },
    } };
    const router = classify === "backend" ? new ConversationRouter(client, "pod", () => {}) : new ConversationRouter(client, "pod", () => {}, async (state) => {
        await delayed?.(); return { ...decision, confidence: 0.95, delivery: state.mode === "event" && state.event?.kind === "completed" ? "speak" : "context" } as any;
    });
    router.subscribe(e => events.push(e));
    return { router, sends, events, creates: () => creates, status: (next: string) => { currentStatus = next; }, frames, reads: () => reads };
}

test("with no route the call carries on: nothing is sent and the voice is told quietly", async () => {
    let failing = true;
    const f = fixture({ action: "voice", conversationId: null }, undefined, "backend", async () => {
        if (failing) throw Object.assign(new Error("Decision provider unavailable"), { statusCode: 503 });
        return { answers: { action: answer("existing"), target: answer("c0") } };
    });
    try {
        await f.router.open("one");
        await f.router.route("u1", "research this", "User: research this");
        assert.equal(f.sends.length, 0); assert.equal(f.creates(), 0);
        assert.equal(f.events.length, 1);
        assert.equal(f.events[0].kind, "failed"); assert.equal(f.events[0].speak, false);
        assert.match(f.events[0].text, /nothing was sent/);
        // The provider is back: the next utterance is routed as usual.
        failing = false;
        await f.router.route("u2", "research this", "User: research this");
        assert.equal(f.sends.length, 1); assert.equal(f.sends[0].id, "one");
    } finally { f.router.close(); }
});

test("an update whose delivery cannot be decided is still delivered when it answers the caller", async () => {
    const f = fixture({ action: "snapshot", conversationId: "one" }, undefined, "backend", async () => { throw new Error("Rate limited"); });
    try {
        f.status("RUNNING");
        await f.router.open("one");
        (f.router as any).responseRequests.set("one", "status-question");
        await (f.router as any).selectEvent("one", "completed", 0);
        assert.equal(f.events.at(-1)!.kind, "completed");
        assert.equal(f.events.at(-1)!.speak, true);
    } finally { f.router.close(); }
});

test("what's up retrieves a snapshot without creating a conversation or starting work", async () => {
    const f = fixture({ action: "snapshot", conversationId: "two" });
    try {
        await f.router.open("one");
        await f.router.route("u1", "what's up with that?", "User: Tell me about two");
        assert.equal(f.sends.length, 0); assert.equal(f.creates(), 0);
        assert.equal(f.events[0].kind, "snapshot");
        assert.match(f.events[0].text, /Found three companies/);
        assert.equal(f.router.focusedId, "two");
    } finally { f.router.close(); }
});

test("new work creates once, repeated request IDs do not send twice, follow-up uses its target", async () => {
    const f = fixture({ action: "new", conversationId: null });
    try {
        await f.router.open(null); assert.equal(f.creates(), 0);
        await Promise.all([f.router.route("u1", "research this", "User: research this"), f.router.route("u1", "research this", "")]);
        assert.equal(f.creates(), 1); assert.equal(f.sends.length, 1);
        assert.equal(f.sends[0].id, "new");
        assert.equal(f.events[0].kind, "accepted");
    } finally { f.router.close(); }
    const followup = fixture({ action: "existing", conversationId: "two" });
    try {
        await followup.router.open("one"); await followup.router.route("u2", "only September", "User: Update two");
        assert.equal(followup.sends[0].id, "two");
        assert.match(followup.sends[0].content, /only September/);
        assert.match(followup.sends[0].content, /Update two/);
    } finally { followup.router.close(); }
});

test("ending a call during classification prevents late dispatch", async () => {
    let release!: () => void;
    const wait = new Promise<void>(resolve => { release = resolve; });
    const f = fixture({ action: "existing", conversationId: "one" }, () => wait);
    await f.router.open("one");
    const result = f.router.route("u1", "do work", "");
    await Promise.resolve(); f.router.close(); release(); await result;
    assert.equal(f.sends.length, 0);
});

test("ending a call abandons a route the backend has not answered", async () => {
    const f = fixture({ action: "voice", conversationId: null }, undefined, "backend", () => new Promise(() => {}));
    await f.router.open("one");
    const result = f.router.route("u1", "do work", "");
    await new Promise(resolve => setTimeout(resolve, 0));
    f.router.close(); await result;
    assert.equal(f.sends.length, 0); assert.equal(f.events.length, 0);
});

test("utterance fragments and immediate corrections dispatch together; closing drops unfinished speech", () => {
    const sent: string[] = [];
    const buffer = new UtteranceBuffer(text => sent.push(text));
    buffer.push("send it", true); buffer.push(" actually wait", true); buffer.flush();
    assert.deepEqual(sent, ["send it actually wait"]);
    buffer.push("unfinished"); buffer.close(); buffer.flush();
    assert.equal(sent.length, 1);
});


test("stream tokens and completion update state without polling message history", async () => {
    const f = fixture({ action: "existing", conversationId: "one" });
    f.frames.push({ type: "token", data: "Streamed result", kind: "text" }, { type: "completed", data: { status: "COMPLETED" } });
    try {
        await f.router.open("one");
        assert.equal(f.reads(), 1);
        await f.router.route("u1", "research", "User: research");
        await new Promise(resolve => setTimeout(resolve, 20));
        assert.equal(f.reads(), 1);
        assert.equal(f.router.snapshots.get("one")?.partialText, "Streamed result");
        assert.equal(f.events.filter(e => e.kind === "completed").length, 1);
    } finally { f.router.close(); }
});

test("clarification does not end the call and the next utterance is routed", async () => {
    const decision = { action: "clarify", conversationId: null as string | null };
    const f = fixture(decision);
    try {
        await f.router.open("one");
        await f.router.route("u1", "that", "");
        assert.equal(f.events[0].kind, "clarify"); assert.equal(f.sends.length, 0);
        decision.action = "existing"; decision.conversationId = "one";
        await f.router.route("u2", "the research", "User: the research");
        assert.equal(f.sends.length, 1);
    } finally { f.router.close(); }
});

test("a call is offered whenever the voice has its key; routing needs none here", async () => {
    const { GET } = await import("../src/app/api/call/config/route.ts");
    const previous = { gemini: process.env.GEMINI_API_KEY, typesafe: process.env.TYPESAFE_API_KEY };
    try {
        delete process.env.TYPESAFE_API_KEY;
        process.env.GEMINI_API_KEY = "test-only";
        assert.deepEqual(await (await GET()).json(), { configured: true });
        delete process.env.GEMINI_API_KEY;
        process.env.TYPESAFE_API_KEY = "test-only";
        assert.deepEqual(await (await GET()).json(), { configured: false });
    } finally {
        for (const [key, value] of [["GEMINI_API_KEY", previous.gemini], ["TYPESAFE_API_KEY", previous.typesafe]] as const) {
            if (value === undefined) delete process.env[key]; else process.env[key] = value;
        }
    }
});

test("voice snapshots contain current evidence without replaying old user instructions", async () => {
    const { snapshotText } = await import("../src/call/conversation-router.ts");
    const text = snapshotText({ ...state.conversations[0], messages: [
        { role: "user", text: "old instruction", at: "1" },
        { role: "assistant", text: "old result", at: "2" },
        { role: "user", text: "latest instruction", at: "3" },
        { role: "assistant", text: "current result", at: "4" },
    ] });
    assert.match(text, /current result/);
    assert.doesNotMatch(text, /old instruction|old result|latest instruction|messages/);
});

test("same work with different transcription IDs is dispatched once", async () => {
    const f = fixture({ action: "new", conversationId: null });
    try {
        await f.router.open(null);
        await f.router.route("a", "Research Iran news", "");
        await f.router.route("b", "Research Iran news!", "");
        assert.equal(f.creates(), 1);
        assert.equal(f.sends.length, 1);
        assert.match(f.events.at(-1)!.text, /already accepted/);
        await f.router.route("c", "Research Iran news again", "");
        assert.equal(f.sends.length, 2);
    } finally { f.router.close(); }
});

test("snapshot observation associates subsequent results with the current question", async () => {
    const f = fixture({ action: "snapshot", conversationId: "one" });
    try {
        f.status("RUNNING");
        await f.router.open("one");
        await f.router.route("status-question", "How is it going?", "");
        // Exercise the same publication path used by the live SSE observer.
        await (f.router as any).selectEvent("one", "completed", 0);
        assert.equal(f.events.at(-1)!.responseTo, "status-question");
        assert.equal(f.events.at(-1)!.speak, true);
        assert.equal(f.sends.length, 0);
    } finally { f.router.close(); }
});
