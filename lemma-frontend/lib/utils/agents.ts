import { humanizeName } from '@/lib/utils/display-name';

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
