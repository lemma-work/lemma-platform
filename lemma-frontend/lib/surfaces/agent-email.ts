/**
 * The address an agent will get, worked out before the agent exists.
 *
 * Every agent is given a mailbox the moment it is created — the backend does it
 * in `provision_agent_email_surface`, off the agent's name and the pod's. That
 * makes the address *derivable*, and this module derives it, so the builder can
 * show someone the address they are about to get instead of promising one. The
 * domain is the only part that isn't derivable; it arrives on the surfaces
 * catalog as `email_domain`.
 *
 * This is a deliberate port of `email_address_allocation.py`, and the only
 * reason to duplicate rather than ask the server is that it runs against a name
 * somebody is still typing. Keep the two in step: the tests here mirror that
 * module's cases, and anything they disagree on is a preview that lies.
 *
 * A preview, not a promise, in one case only: when the plain form is already
 * taken the backend appends a random suffix, which nothing here can predict.
 * That is why {@link buildAgentEmailPreview} is named for what it is.
 */

/** RFC 5321 caps the local part at 64 octets — same constant, same reason. */
export const MAX_LOCAL_PART = 64;

/** Lower-case, hyphenated, safe for the local part of an address. */
export function slugify(value: string | null | undefined, fallback = 'agent'): string {
    const slug = String(value ?? '')
        .trim()
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-+|-+$/g, '');
    return slug || fallback;
}

/** Python's `str.strip("-.")` — both characters, both ends. */
function trimSeparators(value: string): string {
    return value.replace(/^[-.]+|[-.]+$/g, '');
}

/**
 * `{agent}.{pod}` — the local part, before any collision suffix.
 *
 * The agent slug is the identifying half, so when the budget is tight the pod
 * slug is truncated first and the agent name is what survives. `agentName: null`
 * is Lem, which gets the pod slug alone: Lem is not one
 * agent among several, it is the pod answering.
 */
export function buildLocalPart({
    agentName,
    podName,
}: {
    agentName: string | null;
    podName: string | null | undefined;
}): string {
    const pod = slugify(podName, 'pod');
    if (agentName === null) return trimSeparators(pod.slice(0, MAX_LOCAL_PART));

    const agent = slugify(agentName);
    // Everything except the pod slug is fixed; give the pod whatever is left.
    const roomForPod = MAX_LOCAL_PART - agent.length - 1; // 1 for the dot
    if (roomForPod < 1) {
        // A pathologically long agent name: keep the address valid by cutting
        // the agent slug itself, and drop the pod half entirely.
        return trimSeparators(agent.slice(0, MAX_LOCAL_PART));
    }
    return trimSeparators(`${agent}.${pod.slice(0, roomForPod)}`);
}

/**
 * The address this agent is about to be given, or null when this deployment
 * mints none (no `email_domain` in the catalog) or nothing has been named yet.
 */
export function buildAgentEmailPreview({
    agentName,
    podName,
    domain,
}: {
    agentName: string | null;
    podName: string | null | undefined;
    domain: string | null | undefined;
}): string | null {
    const cleanDomain = String(domain ?? '').trim().toLowerCase();
    if (!cleanDomain) return null;
    // An unnamed agent has no address to preview — `slugify` would fall back to
    // the literal "agent", which is a different agent's address soon enough.
    if (agentName !== null && !slugifiable(agentName)) return null;
    return `${buildLocalPart({ agentName, podName })}@${cleanDomain}`;
}

/** Whether a name has anything in it a local part can be built from. */
function slugifiable(value: string): boolean {
    return /[a-z0-9]/.test(value.toLowerCase());
}

export interface SplitEmail {
    /** The identifying half — what makes this address this agent's. */
    local: string;
    /** `@` and the domain, which is the same on every address here. */
    domain: string;
}

/**
 * An address split so the two halves can be weighted differently.
 *
 * Every managed address in a deployment shares one domain, so rendering it at
 * the same weight as the local part spends most of the line on the part that
 * tells you nothing — and, in a chip, truncates away the part that does.
 */
export function splitEmail(address: string | null | undefined): SplitEmail | null {
    const value = String(address ?? '').trim();
    if (!value) return null;
    const at = value.lastIndexOf('@');
    if (at <= 0) return { local: value, domain: '' };
    return { local: value.slice(0, at), domain: value.slice(at) };
}
