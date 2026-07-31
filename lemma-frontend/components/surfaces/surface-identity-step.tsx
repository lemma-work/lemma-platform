'use client';

import Link from 'next/link';
import { Bot, Plug } from '@/components/ui/icons';

import { blockedReason, type CatalogSurface } from '@/lib/surfaces/catalog';
import {
    forAgent,
    type SurfaceIdentityMode,
    type SurfacePlatformDefinition,
} from '@/lib/surfaces/registry';
import { cn } from '@/lib/utils';

/**
 * The first question for every platform with a fork: *whose* bot, number, or
 * workspace. It comes first because the answer decides every later field.
 *
 * An option that can't be picked stays visible and goes disabled with the
 * reason — hiding it would make the org's own state unexplainable ("why is
 * there no Lemma bot option?"). When another pod holds the claim, the reason
 * links to it.
 */
export function SurfaceIdentityStep({
    definition,
    catalog,
    agentName,
    value,
    onChange,
}: {
    definition: SurfacePlatformDefinition;
    catalog: CatalogSurface | null;
    agentName: string | null;
    value: SurfaceIdentityMode | null;
    onChange: (mode: SurfaceIdentityMode) => void;
}) {
    const options = definition.identityOptions;
    if (!options) return null;

    return (
        <div className="surface-choice-stack">
            {options.map((option) => {
                const blocked = blockedReason(catalog, option.mode);
                const selected = value === option.mode;

                return (
                    <div key={option.mode} className="contents">
                        <button
                            type="button"
                            disabled={Boolean(blocked)}
                            onClick={() => onChange(option.mode)}
                            className={cn('surface-identity-option custom-focus-ring', selected && 'is-selected')}
                            aria-pressed={selected}
                        >
                            <span className="surface-choice-icon">
                                {option.mode === 'SYSTEM' ? <Bot className="h-4 w-4" /> : <Plug className="h-4 w-4" />}
                            </span>
                            <span className="min-w-0 flex-1">
                                <span className="surface-identity-option-title">
                                    {option.title}
                                    {option.hint && !blocked ? (
                                        <span className="surface-identity-option-hint">{option.hint}</span>
                                    ) : null}
                                </span>
                                <span className="surface-choice-copy">
                                    {blocked ? blocked.reason : forAgent(option.detail, agentName)}
                                </span>
                            </span>
                        </button>
                        {blocked?.claimedByPodId ? (
                            <Link
                                href={`/pod/${blocked.claimedByPodId}/ai`}
                                className="surface-identity-option-link"
                            >
                                Open the pod using it
                            </Link>
                        ) : null}
                    </div>
                );
            })}
        </div>
    );
}
