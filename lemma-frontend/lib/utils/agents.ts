import { formatSlugTitle } from '@/lib/utils/display-name';

/**
 * "customer-support_bot" → "Customer Support Bot" — for displaying an agent's
 * (slug-like) name as a readable title. Never use this for hrefs, API calls,
 * or anywhere else the raw name is the identifier — display only.
 *
 * Agents were the first resource to need this; apps need the identical thing on
 * home, so the rule itself lives in `formatSlugTitle` and this stays the name
 * the agent surfaces already call.
 */
export function formatAgentName(name: string): string {
    return formatSlugTitle(name);
}
