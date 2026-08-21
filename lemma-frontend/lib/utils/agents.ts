import { humanizeName } from '@/lib/utils/display-name';

/**
 * What the pod's default responder is called, everywhere a person can see it.
 *
 * It had five names — "Pod Assistant" in the sidebar and on its own page,
 * "Lemma Assistant" in the dock and the conversation list, "Pod assistant" in
 * the Slack pickers, `pod_default` on the wire and "the default agent" in the
 * product spec — for one actor that answers most of a pod's conversations.
 *
 * "Assistant" could not be the fix. [docs/product/README.md] fixes the product's
 * nouns against the analytics event catalog, and `assistant` is not among them
 * and appears nowhere in any journey; adopting it would have meant amending an
 * enforced vocabulary to add a *category* that competes with `agent`. A proper
 * name needs no such amendment — Lem is an instance, the way someone's own
 * agent is `triage` — and it is the only form that reads in the first person on
 * the front door, where "Hey, I'm Pod Assistant" was a job title introducing
 * itself.
 *
 * A *lemma* is the step proved so the real result can happen, which is the job.
 * This is display copy only: `pod_default`, `POD_DEFAULT` and
 * `__pod_assistant__` are wire values and stay exactly as they are.
 */
export const DEFAULT_RESPONDER_NAME = 'Lem';

/**
 * What Lem says it does, in its own voice. Lives here rather than on the page
 * so the front door, the agents index and the dock cannot drift apart.
 */
export const DEFAULT_RESPONDER_DESCRIPTION =
    "This pod's most capable agent. Ask me to add a table, build a workflow, spin up an agent, "
    + 'connect a surface, or read and change your data — I act on the pod directly.';

/**
 * "customer-support_bot" → "Customer support bot" — for displaying an agent's
 * (slug-like) name as readable text. Never use this for hrefs, API calls,
 * or anywhere else the raw name is the identifier — display only.
 *
 * Agents were the first resource to need this; apps need the identical thing on
 * home, so the rule itself lives in `humanizeName` and this stays the name
 * the agent surfaces already call.
 */
export function formatAgentName(name: string): string {
    return humanizeName(name);
}

/**
 * Whether an agent declares typed inputs — the line between an agent that is
 * *called* with arguments and one that is *talked to*.
 *
 * This is the same distinction the server publishes as `takes_input` on the
 * agent summary, and the two are not interchangeable: the list endpoint omits
 * `input_schema` entirely, so anything holding a *listed* agent must read the
 * boolean, and only a fully fetched agent can be asked this directly.
 */
export function agentTakesInput(agent: { input_schema?: unknown } | null | undefined): boolean {
    const schema = agent?.input_schema;
    if (!schema || typeof schema !== 'object') return false;

    const properties = (schema as { properties?: unknown }).properties;
    return Boolean(properties && typeof properties === 'object'
        && Object.keys(properties as Record<string, unknown>).length > 0);
}
