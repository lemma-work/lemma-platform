'use client';

import { useMemo } from 'react';
import { ResourceIconUploader } from '@/components/shared/resource-icon-uploader';
import { ResourceIdentity } from '@/components/shared/resource-identity';
import { identityGenes } from '@/lib/identity/seeded-identity';
import { cn } from '@/lib/utils';
import { formatIdentityIcon, identityVariantSeed, parseResourceIcon } from '@/lib/utils/resource-icon-value';

/**
 * Enough faces to find one you like without turning the choice into work. The
 * first is the agent's own — what it already has, and what it keeps if nobody
 * touches this.
 */
const VARIANT_COUNT = 18;

interface AgentAvatarPickerProps {
    value?: string | null;
    name?: string;
    /**
     * What the generated identity is drawn from. Must match the seed the agent
     * is rendered with everywhere else, or the picture someone picks here is
     * not the picture they get.
     */
    seed?: string;
    onChange: (value: string | null) => void;
    compact?: boolean;
}

export function AgentAvatarPicker({
    value,
    name = 'Agent',
    seed,
    onChange,
    compact = false,
}: AgentAvatarPickerProps) {
    const baseSeed = seed || name;
    const icon = useMemo(() => parseResourceIcon(value), [value]);
    const isPicture = icon?.kind === 'url' || icon?.kind === 'glyph';
    const selectedVariant = icon?.kind === 'identity' ? icon.variant : 0;
    /**
     * Offer faces that actually look different from each other.
     *
     * Taking variants 0…17 straight off the counter draws whatever the hash
     * happens to give, which in practice meant four near-identical violet
     * squircles sitting in a row — a choice between things you cannot tell
     * apart is not a choice. Walking further up the counter and keeping only
     * the first face of each tone-and-form pair costs nothing and makes the
     * grid read as a set of options. The agent's own face is always first,
     * whatever it looks like, because that is the one it already has.
     */
    const variants = useMemo(() => {
        const chosen: number[] = [0];
        const seen = new Set<string>();
        const first = identityGenes(identityVariantSeed(baseSeed, 0));
        seen.add(`${first.tone}|${first.form}`);

        for (let variant = 1; variant < 400 && chosen.length < VARIANT_COUNT; variant += 1) {
            const genes = identityGenes(identityVariantSeed(baseSeed, variant));
            const key = `${genes.tone}|${genes.form}`;
            if (seen.has(key)) continue;
            seen.add(key);
            chosen.push(variant);
        }
        return chosen;
    }, [baseSeed]);

    return (
        <div className={cn('space-y-4', compact && 'space-y-3')}>
            <div className="flex items-center gap-3">
                <div
                    className={cn(
                        'flex shrink-0 items-center justify-center overflow-hidden bg-transparent',
                        compact ? 'h-12 w-12' : 'h-16 w-16',
                    )}
                >
                    {isPicture && icon?.kind === 'url' ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img src={icon.url} alt={`${name} profile picture`} className="h-full w-full object-contain p-1.5" />
                    ) : isPicture && icon?.kind === 'glyph' ? (
                        <span className="resource-icon-glyph-box">
                            <span className="resource-icon-glyph" role="img" aria-label={name}>
                                {icon.glyph}
                            </span>
                        </span>
                    ) : (
                        <ResourceIdentity
                            seed={identityVariantSeed(baseSeed, selectedVariant)}
                            label={name}
                            size={compact ? 48 : 64}
                        />
                    )}
                </div>
                <div className="min-w-0">
                    <p className="type-eyebrow">Display picture</p>
                    <p className="mt-1 truncate text-sm font-medium text-[var(--text-primary)]">
                        {isPicture ? 'Custom picture' : selectedVariant === 0 ? 'This agent’s own' : `Variant ${selectedVariant}`}
                    </p>
                </div>
            </div>

            <div>
                <p className="type-eyebrow mb-2">Pick a face</p>
                <div className={cn('grid grid-cols-6 gap-2 sm:grid-cols-9', compact && 'grid-cols-9 gap-1.5')}>
                    {variants.map((variant) => {
                        const selected = !isPicture && variant === selectedVariant;

                        return (
                            <button
                                key={variant}
                                type="button"
                                aria-label={variant === 0 ? `Use ${name}’s own face` : `Use variant ${variant}`}
                                aria-pressed={selected}
                                className={cn(
                                    'agent-avatar-option-button flex aspect-square items-center justify-center rounded-lg border border-transparent bg-transparent p-1 transition-colors hover:bg-[var(--bg-subtle)]',
                                    selected ? 'ring-1 ring-[var(--action-primary)]' : '',
                                )}
                                // Variant 0 is the absence of a choice, so it clears the
                                // field rather than writing "the default" into it.
                                onClick={() => onChange(variant === 0 ? null : formatIdentityIcon(variant))}
                            >
                                <ResourceIdentity
                                    seed={identityVariantSeed(baseSeed, variant)}
                                    label=""
                                    size={compact ? 30 : 36}
                                />
                            </button>
                        );
                    })}
                </div>
            </div>

            <div className={cn('rounded-lg bg-[color:color-mix(in_srgb,var(--surface-2)_44%,transparent)] p-3', compact && 'rounded-md p-2')}>
                <ResourceIconUploader
                    kind="agent"
                    name={name}
                    value={value}
                    onChange={onChange}
                    identitySeed={baseSeed}
                    compact
                    iconClassName={compact ? 'h-8 w-8 rounded-md' : 'h-10 w-10 rounded-lg'}
                />
            </div>
        </div>
    );
}
