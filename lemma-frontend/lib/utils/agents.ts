/**
 * "customer-support_bot" → "Customer Support Bot" — for displaying an agent's
 * (slug-like) name as a readable title. Never use this for hrefs, API calls,
 * or anywhere else the raw name is the identifier — display only.
 */
export function formatAgentName(name: string): string {
    return name
        .replace(/[_-]+/g, ' ')
        .replace(/\s+/g, ' ')
        .trim()
        .split(' ')
        .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
        .join(' ');
}
