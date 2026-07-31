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
 */

const MAX_SLUG_LENGTH = 32;

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
