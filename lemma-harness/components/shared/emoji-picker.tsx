'use client';

import { useState, type ReactNode } from 'react';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Button } from '@/components/ui/button';
import { isResourceIconGlyph } from '@/lib/utils/resource-icon-value';

/**
 * A short, opinionated set rather than the full Unicode table.
 *
 * A complete picker needs a name index to be searchable, and a name index is a
 * dataset — several hundred kilobytes shipped to every page that draws an icon,
 * on a repo that budgets its bundles. These are the ones people actually name a
 * workspace after, and the paste field at the bottom of the popover covers
 * everything else without carrying the dataset.
 *
 * Deliberately no zero-width-joiner sequences (👨‍👩‍👧‍👦, 🧑‍💻): they render
 * inconsistently across platforms and would make the grid ragged. The parser
 * accepts them, so pasting one still works.
 */
const EMOJI_GROUPS: { label: string; emoji: string[] }[] = [
    {
        label: 'Work',
        emoji: [
            '🚀', '📊', '📈', '📉', '🧭', '🗂️', '📋', '📌',
            '📎', '🗓️', '⏱️', '📥', '📤', '✉️', '💬', '🔔',
            '🏷️', '💼', '🧾', '📝', '🗒️', '📁', '🔎', '💰',
        ],
    },
    {
        label: 'Build',
        emoji: [
            '🛠️', '🔧', '🔨', '⚙️', '🧰', '🧪', '🧬', '🔬',
            '🖥️', '💻', '⌨️', '🖱️', '🗄️', '🧮', '🔌', '🔋',
            '📡', '🛰️', '🧱', '🪛', '⚡', '🔗', '🪄', '🧩',
        ],
    },
    {
        label: 'Signals',
        emoji: [
            '🤖', '🧠', '👋', '👍', '🙌', '🫶', '🎯', '🏁',
            '🏆', '⭐', '✨', '🔥', '💡', '❤️', '🎉', '🎈',
            '🕹️', '🎨', '🎭', '🎵', '📷', '🎬', '🧿', '👀',
        ],
    },
    {
        label: 'World',
        emoji: [
            '🌱', '🌳', '🌲', '🍀', '🌸', '🌊', '🌤️', '🌙',
            '☀️', '⛰️', '🏔️', '🗺️', '🌍', '🏠', '🏢', '🏭',
            '🚚', '✈️', '🚢', '🛎️', '🧊', '🍎', '☕', '🐝',
        ],
    },
];

export function EmojiPicker({
    value,
    onSelect,
    onClear,
    disabled,
    children,
}: {
    /** The glyph currently stored, if it is one. */
    value?: string | null;
    onSelect: (glyph: string) => void;
    onClear: () => void;
    disabled?: boolean;
    /** The trigger. Rendered `asChild`, so it must take a ref. */
    children: ReactNode;
}) {
    const [open, setOpen] = useState(false);
    const [pasted, setPasted] = useState('');
    const pastedIsValid = isResourceIconGlyph(pasted);

    const choose = (glyph: string) => {
        onSelect(glyph);
        setPasted('');
        setOpen(false);
    };

    return (
        <Popover
            open={open}
            onOpenChange={(next) => {
                setOpen(next);
                if (!next) setPasted('');
            }}
        >
            <PopoverTrigger asChild disabled={disabled}>
                {children}
            </PopoverTrigger>
            <PopoverContent align="start" className="w-[19.5rem] p-3">
                <div className="mb-2 flex items-center justify-between gap-2">
                    <p className="type-eyebrow text-[var(--text-tertiary)]">Pick an emoji</p>
                    {value ? (
                        <Button
                            variant="quiet"
                            size="xs"
                            onClick={() => {
                                onClear();
                                setOpen(false);
                            }}
                        >
                            Remove
                        </Button>
                    ) : null}
                </div>

                <div className="max-h-64 space-y-3 overflow-y-auto">
                    {EMOJI_GROUPS.map((group) => (
                        <div key={group.label}>
                            <p className="mb-1 text-xs text-[var(--text-tertiary)]">{group.label}</p>
                            <div className="grid grid-cols-8 gap-0.5">
                                {group.emoji.map((glyph) => (
                                    <Button
                                        key={glyph}
                                        variant="quiet"
                                        size="icon"
                                        aria-label={glyph}
                                        aria-pressed={glyph === value}
                                        onClick={() => choose(glyph)}
                                        className="h-8 w-8 p-0 text-lg leading-none data-[selected=true]:bg-[var(--action-primary-soft)]"
                                        data-selected={glyph === value ? 'true' : undefined}
                                    >
                                        {glyph}
                                    </Button>
                                ))}
                            </div>
                        </div>
                    ))}
                </div>

                {/* The escape hatch for everything the grid leaves out — a skin
                    tone, a flag, a family. Cheaper than shipping a name index. */}
                <div className="mt-3 border-t border-[var(--row-border)] pt-3">
                    <div className="flex items-center gap-2">
                        <input
                            value={pasted}
                            onChange={(event) => setPasted(event.target.value)}
                            onKeyDown={(event) => {
                                if (event.key === 'Enter' && pastedIsValid) {
                                    event.preventDefault();
                                    choose(pasted.trim());
                                }
                            }}
                            placeholder="Or paste any emoji"
                            aria-label="Paste an emoji"
                            className="form-field-control h-8 min-w-0 flex-1 px-2.5 text-sm text-[var(--text-primary)] outline-none placeholder:text-[var(--text-soft)] focus-ring"
                        />
                        <Button
                            variant="secondary"
                            size="xs"
                            disabled={!pastedIsValid}
                            onClick={() => choose(pasted.trim())}
                        >
                            Use
                        </Button>
                    </div>
                </div>
            </PopoverContent>
        </Popover>
    );
}
