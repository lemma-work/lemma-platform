'use client';

import { useState } from 'react';
import { toast } from 'sonner';

import { Check, Copy } from '@/components/ui/icons';
import { usePod } from '@/lib/hooks/use-pods';
import { buildContactLink } from '@/lib/share/share-link';
import type { ContactCardSpec } from '@/lib/share/contact-card';
import { contactChannels } from '@/lib/share/contact-card';
import { agentEmailAddress, getSurfaceIdentity, getSurfacePlatformKey } from '@/lib/utils/surfaces';
import type { AssistantSurface } from '@/lib/types';

interface AgentContactShareProps {
    podId: string;
    /**
     * Where this responder lives in the workspace, e.g.
     * `/pod/p1/agents/support_triage` or `/pod/p1/ai/assistant` for Lem. The
     * share link wraps it, and the card page offers it as "Open it in Lemma".
     */
    workspacePath: string;
    /** The display name, which is what goes on the card someone reads. */
    agentName: string;
    /** What the face is drawn from — an agent's identity, or `LEM_SEED` for Lem. */
    seed: string;
    iconUrl?: string | null;
    description?: string | null;
    /** True for Lem, whose name is the same in every pod. */
    isAssistant?: boolean;
    /**
     * Only the surfaces that reach *this* responder, already filtered by the
     * caller with the same predicate the reach row above uses. Passing the full
     * list would advertise a bot whose DMs belong to somebody else.
     */
    surfaces: AssistantSurface[];
}

/** The handle a platform publishes, preferring the resolved reach over the stored one. */
function handleFor(surfaces: AssistantSurface[], platform: string): string | null {
    for (const surface of surfaces) {
        if (getSurfacePlatformKey(surface) !== platform) continue;
        const handle = surface.reach?.handle || getSurfaceIdentity(surface);
        if (handle) return handle;
    }
    return null;
}

/**
 * The card, as the agent's own page can mint it.
 *
 * Minting happens here because this is where the reach already is: the row above
 * has just told someone they can talk to this agent on Telegram or WhatsApp, and
 * the obvious next thought is to pass that on to a person who does not have a
 * Lemma account. Everything the card needs is already loaded — no second fetch,
 * and no endpoint that has to decide whether a stranger may ask.
 *
 * What it copies is a link, not a file. The `.vcf` is one click further on, on a
 * page that also unfurls in the chat someone pastes it into — and a preview card
 * with the agent's face on it is what makes the link worth opening. A bare file
 * attachment says nothing until it is already saved.
 *
 * **Lem gets one too.** It is the pod's front door, so its card is the most
 * useful one a pod can hand out — but it is also the same name and the same face
 * in every pod, which no other agent is. So its card is named for the pod it
 * answers for; without that, saving Lem from two pods leaves an address book with
 * two identical rows and no way to tell which is which.
 */
export function AgentContactShare({
    podId,
    workspacePath,
    agentName,
    seed,
    iconUrl,
    description,
    isAssistant,
    surfaces,
}: AgentContactShareProps) {
    const [copied, setCopied] = useState(false);
    // Already in cache — the workspace shell reads the same key on the way in.
    const { data: pod } = usePod(podId);
    const podName = pod?.name?.trim() || null;

    const card: ContactCardSpec = {
        // Lem's own name identifies it inside a pod and nowhere else. An address
        // book is the "nowhere else".
        name: isAssistant && podName ? `${agentName} · ${podName}` : agentName,
        seed,
        icon: iconUrl,
        org: podName,
        note: description,
        telegram: handleFor(surfaces, 'TELEGRAM'),
        whatsapp: handleFor(surfaces, 'WHATSAPP'),
        email: agentEmailAddress(surfaces),
    };

    // Nowhere to reach it means no card worth sending. The reach row above still
    // has something to say — "connect Telegram" — but a contact carrying no
    // address is a promise this cannot keep.
    //
    // A handle shared with other pods is *not* that case, though an earlier
    // version of this treated it as one. One number fronting many pods is the
    // deployment's design, not a fault: a DM to it asks which pod was meant
    // (`contended_surface_ids`), which is a step rather than a dead end — and
    // excluding it hid the card from every pod that had not brought a bot of
    // its own, which is nearly all of them.
    if (contactChannels(card).length === 0) return null;

    const copy = async () => {
        const link = buildContactLink({
            canonicalUrl: `${window.location.origin}${workspacePath}`,
            card,
        });
        if (!link) {
            toast.error('Could not build a contact link');
            return;
        }
        try {
            await navigator.clipboard.writeText(link);
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
        } catch {
            toast.error('Could not copy to clipboard');
        }
    };

    return (
        <button
            type="button"
            onClick={copy}
            className="agent-home-reach-chip custom-focus-ring"
            title="A link anyone can open to save this agent to their contacts"
        >
            {copied ? (
                <Check className="h-4 w-4" aria-hidden="true" />
            ) : (
                <Copy className="h-4 w-4" aria-hidden="true" />
            )}
            <span>{copied ? 'Link copied' : 'Share my contact card'}</span>
        </button>
    );
}
