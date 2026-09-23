'use client';

import { use, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import { toast } from 'sonner';

import { RefreshCw } from '@/components/ui/icons';

import { AgentEmail } from '@/components/surfaces/agent-email';
import { ResourceIcon } from '@/components/shared/resource-icon';
import {
    ResourceDetailShell,
    ResourceDetailViewport,
    ResourceHeader,
} from '@/components/pod/resource-layout';
import { showResourceCreatedToast, showResourceErrorToast } from '@/components/shared/resource-feedback';
import { Button } from '@/components/ui/button';
import { useCreateAgent } from '@/lib/hooks/use-agents';
import { usePodAccess } from '@/lib/hooks/use-pod-access';
import { useAvailableSurfaces } from '@/lib/hooks/use-pod-surfaces';
import { usePod } from '@/lib/hooks/use-pods';
import { distinctIdentityVariants, formatIdentityIcon, identityVariantSeed } from '@/lib/utils/resource-icon-value';
import { buildAgentEmailPreview } from '@/lib/surfaces/agent-email';
import { managedEmailDomain } from '@/lib/surfaces/catalog';

/**
 * Making an agent is one step: a face, a name, a purpose.
 *
 * It was five — identity, instructions, shape, access, review — which asked for
 * an agent's whole contract before it existed. That ordering only made sense
 * when the agent's own page was an editor you had to arrive at fully formed.
 * It is a home now, with Configure on it, so everything the wizard front-loaded
 * has a better moment later: you tune an agent you can already talk to, against
 * runs you have already seen, instead of guessing at its schema and its table
 * access from a blank form.
 *
 * The three that remain are the three that cannot come later. A name is the
 * address — it is what makes the agent reachable from outside Lemma at all — a
 * face is how it will be told apart from every other agent in the rail, and a
 * purpose is what its first instruction is written from.
 *
 * The form is shaped like the page it makes. Same column, same face at the same
 * size, name where the greeting goes and purpose where the description goes —
 * so creating an agent is filling in its front door, and pressing Create lands
 * on the finished version of the thing you were just looking at.
 */
/** Eight, like a row of faces should be — enough to choose from, few enough to compare. */
const FACE_OPTIONS = 8;

/** The name seeds the face, and a picked variant shifts which face that is. */
function seedFor(name: string, variant: number) {
    return identityVariantSeed(name || 'Untitled agent', variant);
}

export default function NewAgentPage({ params }: { params: Promise<{ id: string }> }) {
    const { id: podId } = use(params);
    const router = useRouter();
    const podAccess = usePodAccess(podId);
    const createAgent = useCreateAgent();

    const [name, setName] = useState('');
    const [purpose, setPurpose] = useState('');
    const [iconUrl, setIconUrl] = useState<string | undefined>(undefined);
    const [variant, setVariant] = useState(0);
    const [facePage, setFacePage] = useState(0);

    const { data: pod } = usePod(podId);
    const { data: surfaceCatalog } = useAvailableSurfaces(podId);
    const emailDomain = managedEmailDomain(surfaceCatalog);
    const trimmedName = name.trim();
    const emailPreview = useMemo(
        () => buildAgentEmailPreview({
            agentName: trimmedName || null,
            podName: pod?.name,
            domain: emailDomain,
        }),
        [emailDomain, pod?.name, trimmedName],
    );

    /* The face is seeded from the name, so it changes as the name is typed —
       which is right (the agent's own page seeds the same way) but means the
       eight options have to re-seed with it, not stay pinned to whatever the
       field said when the page loaded. */
    const faceVariants = useMemo(
        () => distinctIdentityVariants(trimmedName || 'Untitled agent', FACE_OPTIONS, facePage * FACE_OPTIONS),
        [facePage, trimmedName],
    );

    const create = async () => {
        if (!trimmedName) {
            toast.error('Please name the agent first');
            return;
        }

        try {
            const newAgent = await createAgent.mutateAsync({
                podId,
                data: {
                    name: trimmedName,
                    description: purpose.trim() || null,
                    icon_url: iconUrl || undefined,
                    // The purpose is the first instruction. An agent with none is
                    // inert, and asking for a prompt before there is anything to
                    // test it against is what the wizard's second step was.
                    instruction: defaultInstruction(trimmedName, purpose),
                },
            });

            showResourceCreatedToast('Agent', newAgent.name);
            router.push(`/pod/${podId}/agents/${encodeURIComponent(newAgent.name)}?created=agent`);
        } catch (error) {
            console.error('Failed to create agent:', error);
            showResourceErrorToast(error, 'Failed to create agent');
        }
    };

    if (!podAccess.isLoading && !podAccess.can('agent.create')) {
        return (
            <div className="flex h-full items-center justify-center bg-transparent px-4">
                <div className="surface-panel max-w-lg p-6 text-center sm:p-8">
                    <h2 className="mb-2 font-display text-xl font-semibold text-[var(--text-primary)]">
                        No access to create agents
                    </h2>
                    <p className="text-sm text-[var(--text-secondary)]">
                        Ask a pod admin for permission to add agents to this pod.
                    </p>
                </div>
            </div>
        );
    }

    return (
        <ResourceDetailShell>
            {/* The bar stays here, unlike the agent's own home. A home has moved
                its chrome into the page and has a tab naming it; this is a task
                you arrive at and leave from, and without a header the two columns
                simply floated — which is what made a bordered card feel necessary
                in the first place. The bar is the page furniture; the card was
                standing in for it. */}
            <ResourceHeader
                title="New agent"
                backHref={`/pod/${podId}/ai`}
                backLabel="Agents"
                fullscreen={false}
            />

            <ResourceDetailViewport>
                <div className="resource-page-scroll agent-home-scroll">
                    <form
                        className="agent-create-card"
                        onSubmit={(event) => {
                            event.preventDefault();
                            void create();
                        }}
                    >
                        {/* The preview is its own panel, not the fields wearing
                            the result's clothes. That was the earlier attempt and
                            it cost the fields their affordances; a face and a name
                            standing on their own cost nothing and show the same
                            thing better. */}
                        <div className="agent-create-preview">
                            <ResourceIcon
                                iconUrl={iconUrl}
                                alt=""
                                label={trimmedName || 'New agent'}
                                identityKind="being"
                                identitySeed={seedFor(trimmedName, variant)}
                                identitySize={96}
                                className="h-24 w-24 rounded-3xl"
                            />
                            <p className="agent-create-preview-name" data-empty={trimmedName ? undefined : 'true'}>
                                {trimmedName || 'Your agent'}
                            </p>
                        </div>

                        <div className="agent-create-fields">
                            <label className="agent-create-field">
                                <span className="agent-create-label">Name</span>
                                <input
                                    value={name}
                                    onChange={(event) => setName(event.target.value)}
                                    placeholder="Pitch polish"
                                    autoFocus
                                    className="form-field-control h-10 w-full px-3 text-sm text-[var(--text-primary)] outline-none placeholder:text-[var(--text-soft)]"
                                />
                            </label>

                            {/* Under the name, because the name is what draws it.
                                An identity stores a *variant*, not a picture —
                                the seed is always the resource's own name — so
                                the face is a function of both, and typing after
                                picking one turned it into a different creature.
                                That is the system working, not a slip: renaming
                                an agent changes its face wherever it appears.
                                Asking for the name first puts cause before
                                effect, and the note says the rule out loud so it
                                reads as designed rather than as the picker
                                losing your choice.
                                Inline, always: hiding eight faces behind a
                                toggle made picking one a thing you had to go
                                looking for, and the face is half of how an agent
                                is told apart in a rail of them. */}
                            <div className="agent-create-row">
                                <span className="agent-create-label">
                                    Character
                                    <span className="agent-create-hint">drawn from the name</span>
                                </span>
                                <button
                                    type="button"
                                    onClick={() => setFacePage((page) => page + 1)}
                                    className="agent-create-reshuffle custom-focus-ring"
                                >
                                    <RefreshCw className="h-3.5 w-3.5" />
                                    More options
                                </button>
                            </div>
                            <div className="agent-create-faces">
                                {faceVariants.map((option) => (
                                    <button
                                        key={option}
                                        type="button"
                                        onClick={() => {
                                            setVariant(option);
                                            setIconUrl(formatIdentityIcon(option));
                                        }}
                                        data-active={option === variant ? 'true' : undefined}
                                        aria-label={`Face option ${option + 1}`}
                                        aria-pressed={option === variant}
                                        className="agent-create-face custom-focus-ring"
                                    >
                                        <ResourceIcon
                                            alt=""
                                            label={`Face ${option + 1}`}
                                            identityKind="being"
                                            identitySeed={seedFor(trimmedName, option)}
                                            identitySize={36}
                                            className="h-9 w-9 rounded-lg"
                                        />
                                    </button>
                                ))}
                            </div>

                            <label className="agent-create-field">
                                <span className="agent-create-label">Purpose</span>
                                <textarea
                                    value={purpose}
                                    onChange={(event) => setPurpose(event.target.value.slice(0, 200))}
                                    /* The placeholder is an example, not an
                                       instruction. "What should it help with?"
                                       asks the question again; a written purpose
                                       shows the length and the register of a good
                                       answer. */
                                    placeholder="Turns rough ideas into sharp, memorable pitches"
                                    rows={2}
                                    className="form-field-control w-full resize-none px-3 py-2.5 text-sm leading-6 text-[var(--text-secondary)] outline-none placeholder:text-[var(--text-soft)]"
                                />
                            </label>

                            {/* The name is not only a label — it decides the
                                address, and the address is what makes an agent
                                reachable by someone who never opens Lemma. */}
                            {trimmedName && emailPreview ? (
                                <p className="agent-builder-email-note">
                                    <span className="text-[var(--text-secondary)]">People will be able to email it at</span>
                                    <AgentEmail address={emailPreview} size="sm" preview />
                                </p>
                            ) : null}

                            <Button
                                type="submit"
                                variant="primary"
                                className="agent-create-submit"
                                disabled={!trimmedName}
                                loading={createAgent.isPending}
                                loadingLabel="Creating…"
                            >
                                Get started
                            </Button>

                            <p className="agent-create-note">
                                Instructions, what it can reach, and its schedules are all
                                set from the agent&rsquo;s own page afterwards.
                            </p>
                        </div>
                    </form>
                </div>
            </ResourceDetailViewport>
        </ResourceDetailShell>
    );
}

function defaultInstruction(name: string, description?: string | null) {
    const subject = name.trim() || 'this agent';
    const purpose = description?.trim();
    return purpose
        ? `You are ${subject}. ${purpose} Be clear, useful, and stay within the pod context and granted tools.`
        : `You are ${subject}. Help the user with the task they bring to you. Be clear, useful, and stay within the pod context and granted tools.`;
}
