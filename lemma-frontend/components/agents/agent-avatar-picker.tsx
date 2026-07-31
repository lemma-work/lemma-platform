'use client';

import { Bot } from '@/components/ui/icons';
import { AGENT_MASCOTS } from '@/lib/data/agent-mascots';
import { ResourceIconUploader } from '@/components/shared/resource-icon-uploader';
import { cn } from '@/lib/utils';

interface AgentAvatarPickerProps {
    value?: string | null;
    name?: string;
    onChange: (value: string | null) => void;
    compact?: boolean;
}

export function AgentAvatarPicker({
    value,
    name = 'Agent',
    onChange,
    compact = false,
}: AgentAvatarPickerProps) {
    const selectedMascot = AGENT_MASCOTS.find((mascot) => mascot.src === value);

    return (
        <div className={cn('space-y-3', compact && 'space-y-2')}>
            <div className="flex items-center gap-3">
                <div className={cn(
                    'flex shrink-0 items-center justify-center overflow-hidden bg-transparent',
                    compact ? 'h-12 w-12 rounded-xl' : 'h-24 w-24'
                )}>
                    {value ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img src={value} alt={`${name} profile picture`} className="h-full w-full object-contain p-1.5" />
                    ) : (
                        <Bot className={cn('text-[var(--text-tertiary)]', compact ? 'h-6 w-6' : 'h-8 w-8')} />
                    )}
                </div>
                <div className="min-w-0">
                    <p className="type-eyebrow">
                        Display picture
                    </p>
                    <p className="mt-1 truncate text-sm font-medium text-[var(--text-primary)]">
                        {selectedMascot?.label || 'Pick a Lemma mascot'}
                    </p>
                    <p className={cn('mt-0.5 text-xs leading-5 text-[var(--text-secondary)]', compact && 'hidden')}>
                        Local assets from the Lemma brand set.
                    </p>
                </div>
            </div>

            <div className={cn('grid grid-cols-5 gap-2 sm:grid-cols-9', compact && 'grid-cols-9 gap-1.5')}>
                {AGENT_MASCOTS.map((mascot) => {
                    const selected = mascot.src === value;

                    return (
                        <button
                            key={mascot.id}
                            type="button"
                            title={mascot.label}
                            aria-label={`Use ${mascot.label} mascot`}
                            className={cn(
                                'agent-avatar-option-button flex aspect-square items-center justify-center rounded-lg border border-transparent bg-transparent p-1.5 transition-colors hover:bg-[var(--bg-subtle)]',
                                compact && 'rounded-lg p-1',
                                selected
                                    ? 'ring-1 ring-[var(--delight)]'
                                    : ''
                            )}
                            onClick={() => onChange(mascot.src)}
                        >
                            {/* eslint-disable-next-line @next/next/no-img-element */}
                            <img src={mascot.src} alt="" className="h-full w-full object-contain" />
                        </button>
                    );
                })}
            </div>

            <div className={cn(
                'rounded-lg bg-[color:color-mix(in_srgb,var(--surface-2)_44%,transparent)] p-3',
                compact && 'rounded-md p-2'
            )}>
                <ResourceIconUploader
                    kind="agent"
                    name={name}
                    value={value}
                    onChange={onChange}
                    compact
                    iconClassName={compact ? 'h-8 w-8 rounded-md' : 'h-10 w-10 rounded-lg'}
                />
            </div>
        </div>
    );
}
