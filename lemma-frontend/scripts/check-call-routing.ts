/** Opt-in live check of call routing; synthetic state only, and it never
 *  dispatches work or keeps a decision.
 *
 *  It asks a running backend's decisions API as the person a token belongs
 *  to, in a pod that person can use, so the engine that answers is the
 *  backend's: System One when the backend has TYPESAFE_API_KEY, its system
 *  model when not. Run from lemma-frontend, with the SDK built:
 *
 *    LEMMA_BASE_URL=https://api.example.com LEMMA_TOKEN=<token> LEMMA_POD_ID=<pod> \
 *      node --experimental-strip-types scripts/check-call-routing.ts
 *
 *  CALL_CHECK_CASE=<name> runs one case. CALL_CHECK_DETAILS=1 prints each
 *  decision's answers, including which engine gave them. */
import env from "@next/env";
import { LemmaClient } from "lemma-sdk";
import { routeWithDecisions } from "../src/call/jev-router.ts";
import type { ConversationSnapshot, RouteAsk, RouterState } from "../src/call/routing.ts";
env.loadEnvConfig(process.cwd());
const podId = process.env.LEMMA_POD_ID;
if (!process.env.LEMMA_BASE_URL || !process.env.LEMMA_TOKEN || !podId) throw new Error("Set LEMMA_BASE_URL, LEMMA_TOKEN and LEMMA_POD_ID first");
// The SDK reads the same three variables.
const client = new LemmaClient();
const ask = (name: string): RouteAsk => ({ podId, client: { request: async (method: string, path: string, options?: object) => {
    const decision = await client.request(method, path, options);
    if (process.env.CALL_CHECK_DETAILS) console.log(JSON.stringify({ name, answers: (decision as { answers?: unknown }).answers }));
    return decision;
} } as RouteAsk["client"] });
const snapshot = (id: string, title: string, text: string): ConversationSnapshot => ({
    id, title, status: "RUNNING", updatedAt: new Date().toISOString(), fetchedAt: new Date().toISOString(),
    lastRunStatus: "RUNNING", error: null, messages: [{ role: "user", text, at: new Date().toISOString() }], plan: [], resources: [], needsInput: false, openQuestions: [],
});
const base: RouterState = { mode: "utterance", utterance: "", transcript: "User: Research Indian SaaS companies.\nVoice: The research is underway.",
    podContext: "A business research workspace", focusedConversationId: "research",
    conversations: [snapshot("research", "Indian SaaS research", "Research Indian SaaS companies for September."), snapshot("website", "Website redesign", "Redesign the product website.")] };
const cases: Array<{ name: string; state: RouterState; action: string; target?: string | null; delivery?: string }> = [
    { name: "world news", state: { ...base, utterance: "What happened in the world today?" }, action: "new" },
    { name: "status snapshot", state: { ...base, utterance: "What's up with that?" }, action: "snapshot", target: "research" },
    { name: "correction", state: { ...base, utterance: "Actually only include the Indian ones founded in September." }, action: "existing", target: "research" },
    { name: "fresh check", state: { ...base, utterance: "Check for new funding announcements for those companies today." }, action: "existing", target: "research" },
    { name: "separate work", state: { ...base, utterance: "Start a separate conversation and plan a birthday dinner for my brother." }, action: "new" },
    { name: "acknowledgement", state: { ...base, utterance: "Thanks, sounds good." }, action: "voice" },
    { name: "ambiguous reference", state: { ...base, focusedConversationId: null, transcript: "", utterance: "Change that one." }, action: "clarify" },
    { name: "quiet progress", state: { ...base, mode: "event", event: { id: "e1", conversationId: "research", kind: "progress", text: "Still collecting company sources. No new findings.", speak: false } }, action: "voice", delivery: "context" },
    { name: "relevant completion", state: { ...base, mode: "event", event: { id: "e2", conversationId: "research", kind: "completed", text: "Research completed. Five matching companies found; report ready.", speak: false } }, action: "voice", delivery: "speak" },
];
const results = [];
for (const c of cases.filter(c => !process.env.CALL_CHECK_CASE || c.name === process.env.CALL_CHECK_CASE)) {
    const start = performance.now();
    // No subject: a synthetic case is asked afresh each run and never recorded.
    const decision = await routeWithDecisions(c.state, ask(c.name));
    const pass = decision.action === c.action && (c.target === undefined || decision.conversationId === c.target) && (!c.delivery || decision.delivery === c.delivery);
    const row = { name: c.name, pass, ms: Math.round(performance.now() - start), ...decision };
    results.push(row); console.log(JSON.stringify(row));
}
console.log(JSON.stringify({ passed: results.filter(r => r.pass).length, total: results.length }));
if (results.some(r => !r.pass)) process.exitCode = 1;
