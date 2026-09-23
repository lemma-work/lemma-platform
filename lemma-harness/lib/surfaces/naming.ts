import type { AssistantSurface } from '@/lib/types';

/**
 * Picking the name for a surface about to be created.
 *
 * Surfaces are independent: a pod can hold several of the same platform, each
 * with its own bot or account, each answering as a different agent. They're
 * addressed by a pod-unique name that defaults to the platform, so only the
 * *first* of a platform can take that default — every later one needs a name of
 * its own or creation fails with a name collision.
 *
 * The name isn't worth asking about, so it's derived from the agent the surface
 * will answer as, which is both stable and the most useful thing to read in a
 * list. `undefined` means "let the backend apply its default".
 *
 * Except for the platforms the backend gives an identity of its own — see
 * `BACKEND_NAMES_ITS_OWN`. Deriving a name here means reproducing a backend
 * rule from a surfaces list this client may have fetched before the surface it
 * needs to know about existed, and the two only agree while nobody changes
 * either.
 */

const MAX_SLUG_LENGTH = 32;

/**
 * Platforms whose surface names the backend derives itself.
 *
 * Resend is Lemma's own mailbox: a pod holds exactly one per agent, minted as
 * the agent is created, and the backend names it after that agent and resolves
 * it by agent binding rather than by name. Sending a name asks it to create a
 * *distinct* surface instead of connecting the one that already exists, which
 * is not what "connect email" means.
 *
 * Everything else stays client-derived. A pod really can hold several Telegram
 * bots or Slack apps, the backend has no per-agent identity for them, and the
 * second one genuinely does need a name to avoid colliding with the first.
 */
const BACKEND_NAMES_ITS_OWN = new Set(['RESEND']);

function slugify(value: string): string {
    return value
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-+|-+$/g, '')
        .slice(0, MAX_SLUG_LENGTH)
        .replace(/-+$/g, '');
}

export function deriveSurfaceName(
    platform: string,
    agentName: string | null,
    existing: Pick<AssistantSurface, 'name'>[],
): string | undefined {
    if (BACKEND_NAMES_ITS_OWN.has(platform.toUpperCase())) return undefined;

    const base = platform.toLowerCase();
    const taken = new Set(existing.map((surface) => surface.name.toLowerCase()));

    // The first surface of a platform takes the bare platform name, which is
    // what the backend would have chosen anyway.
    if (!taken.has(base)) return undefined;

    const suffix = agentName ? slugify(agentName) : 'default';
    const preferred = suffix ? `${base}-${suffix}` : base;
    if (!taken.has(preferred)) return preferred;

    // Same agent, second bot — or two agents whose names slugify alike.
    for (let n = 2; n < 100; n += 1) {
        const candidate = `${preferred}-${n}`;
        if (!taken.has(candidate)) return candidate;
    }
    return `${preferred}-${taken.size + 1}`;
}
